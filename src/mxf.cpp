#include "mxf/mxf.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

namespace mxf {

namespace {

constexpr size_t kNil = static_cast<size_t>(-1);

Uid make_uid(std::initializer_list<uint8_t> bytes) {
    Uid uid{};
    size_t i = 0;
    for (uint8_t byte : bytes) {
        if (i < uid.size()) {
            uid[i++] = byte;
        }
    }
    return uid;
}

bool starts_with(const Uid& key, std::initializer_list<uint8_t> prefix,
                 size_t length) {
    if (length > key.size() || length > prefix.size()) {
        return false;
    }
    size_t i = 0;
    for (uint8_t byte : prefix) {
        if (i >= length) {
            break;
        }
        if (key[i++] != byte) {
            return false;
        }
    }
    return i == length;
}

uint16_t read_u16(const uint8_t* data) {
    return static_cast<uint16_t>((static_cast<uint16_t>(data[0]) << 8) |
                                 data[1]);
}

uint32_t read_u32(const uint8_t* data) {
    return (static_cast<uint32_t>(data[0]) << 24) |
           (static_cast<uint32_t>(data[1]) << 16) |
           (static_cast<uint32_t>(data[2]) << 8) | data[3];
}

uint64_t read_u64(const uint8_t* data) {
    return (static_cast<uint64_t>(read_u32(data)) << 32) |
           read_u32(data + 4);
}

struct Klv {
    Uid key{};
    uint64_t length = 0;
    size_t offset = 0;
    size_t value_offset = 0;
    size_t next_offset = 0;
    bool valid = false;
};

bool read_klv(const std::vector<uint8_t>& data, size_t offset, Klv& klv) {
    if (offset + 17 > data.size()) {
        return false;
    }
    std::copy_n(data.begin() + static_cast<std::ptrdiff_t>(offset), 16,
                klv.key.begin());
    size_t cursor = offset + 16;
    const uint8_t first = data[cursor++];
    uint64_t length = 0;
    if (first < 0x80) {
        length = first;
    } else {
        const int count = first & 0x7f;
        if (count == 0 || count > 8 || cursor + static_cast<size_t>(count) > data.size()) {
            return false;
        }
        for (int i = 0; i < count; ++i) {
            length = (length << 8) | data[cursor++];
        }
    }
    if (length > data.size() - cursor) {
        return false;
    }
    klv.offset = offset;
    klv.length = length;
    klv.value_offset = cursor;
    klv.next_offset = cursor + static_cast<size_t>(length);
    klv.valid = true;
    return true;
}

bool is_partition_key(const Uid& key) {
    static const std::initializer_list<uint8_t> prefix = {
        0x06, 0x0e, 0x2b, 0x34, 0x02, 0x05, 0x01,
        0x01, 0x0d, 0x01, 0x02, 0x01, 0x01};
    return starts_with(key, prefix, 13) && key[13] >= 2 && key[13] <= 4;
}

bool is_primer_key(const Uid& key) {
    return key == make_uid({0x06, 0x0e, 0x2b, 0x34, 0x02, 0x05, 0x01, 0x01,
                            0x0d, 0x01, 0x02, 0x01, 0x01, 0x05, 0x01, 0x00});
}

bool is_index_key(const Uid& key) {
    return key == make_uid({0x06, 0x0e, 0x2b, 0x34, 0x02, 0x53, 0x01, 0x01,
                            0x0d, 0x01, 0x02, 0x01, 0x01, 0x10, 0x01, 0x00});
}

bool is_metadata_set_key(const Uid& key) {
    static const std::initializer_list<uint8_t> prefix = {
        0x06, 0x0e, 0x2b, 0x34, 0x02, 0x53,
        0x01, 0x01, 0x0d, 0x01, 0x01, 0x01};
    return starts_with(key, prefix, 12);
}

bool is_fill_key(const Uid& key) {
    return key == make_uid({0x06, 0x0e, 0x2b, 0x34, 0x01, 0x01, 0x01, 0x02,
                            0x03, 0x01, 0x02, 0x10, 0x01, 0x00, 0x00, 0x00});
}

bool is_essence_key(const Uid& key) {
    static const std::initializer_list<uint8_t> standard = {
        0x06, 0x0e, 0x2b, 0x34, 0x01, 0x02,
        0x01, 0x01, 0x0d, 0x01, 0x03, 0x01};
    static const std::initializer_list<uint8_t> avid = {
        0x06, 0x0e, 0x2b, 0x34, 0x01, 0x02,
        0x01, 0x01, 0x0e, 0x04, 0x03, 0x01};
    static const std::initializer_list<uint8_t> canopus = {
        0x06, 0x0e, 0x2b, 0x34, 0x01, 0x02,
        0x01, 0x0a, 0x0e, 0x0f, 0x03, 0x01};
    return starts_with(key, standard, 12) ||
           starts_with(key, avid, 12) ||
           starts_with(key, canopus, 12);
}

struct TagValue {
    uint16_t tag = 0;
    std::vector<uint8_t> value;
};

struct RawSet {
    size_t offset = 0;
    Uid key{};
    Uid instance_uid{};
    std::vector<TagValue> tags;
};

struct RawIndex {
    RawSet set;
    IndexSegment segment;
};

std::vector<uint8_t> read_file(const std::string& path, std::string& error) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        error = "cannot open " + path;
        return {};
    }
    input.seekg(0, std::ios::end);
    const auto size = input.tellg();
    if (size < 0) {
        error = "cannot determine file size";
        return {};
    }
    input.seekg(0, std::ios::beg);
    std::vector<uint8_t> data(static_cast<size_t>(size));
    if (!data.empty()) {
        input.read(reinterpret_cast<char*>(data.data()),
                   static_cast<std::streamsize>(data.size()));
    }
    if (!input && !input.eof()) {
        error = "cannot read file";
        return {};
    }
    return data;
}

RawSet parse_local_set(const std::vector<uint8_t>& data, const Klv& klv) {
    RawSet set;
    set.offset = klv.offset;
    set.key = klv.key;
    size_t cursor = klv.value_offset;
    const size_t end = klv.value_offset + static_cast<size_t>(klv.length);
    while (cursor + 4 <= end) {
        TagValue item;
        item.tag = read_u16(data.data() + cursor);
        const uint16_t size = read_u16(data.data() + cursor + 2);
        cursor += 4;
        if (cursor + size > end) {
            break;
        }
        item.value.assign(data.begin() + static_cast<std::ptrdiff_t>(cursor),
                          data.begin() +
                              static_cast<std::ptrdiff_t>(cursor + size));
        if (item.tag == 0x3c0a && item.value.size() == 16) {
            std::copy(item.value.begin(), item.value.end(),
                      set.instance_uid.begin());
        }
        set.tags.push_back(std::move(item));
        cursor += size;
    }
    return set;
}

const TagValue* find_tag(const RawSet& set, uint16_t tag) {
    for (const auto& item : set.tags) {
        if (item.tag == tag) {
            return &item;
        }
    }
    return nullptr;
}

uint64_t tag_u64(const RawSet& set, uint16_t tag, uint64_t fallback = 0) {
    const TagValue* item = find_tag(set, tag);
    if (!item || item->value.size() < 8) {
        return fallback;
    }
    return read_u64(item->value.data());
}

uint32_t tag_u32(const RawSet& set, uint16_t tag, uint32_t fallback = 0) {
    const TagValue* item = find_tag(set, tag);
    if (!item || item->value.size() < 4) {
        return fallback;
    }
    return read_u32(item->value.data());
}

uint16_t tag_u16(const RawSet& set, uint16_t tag, uint16_t fallback = 0) {
    const TagValue* item = find_tag(set, tag);
    if (!item || item->value.size() < 2) {
        return fallback;
    }
    return read_u16(item->value.data());
}

Uid tag_uid(const RawSet& set, uint16_t tag) {
    Uid uid{};
    const TagValue* item = find_tag(set, tag);
    if (item && item->value.size() >= 16) {
        std::copy_n(item->value.begin(), 16, uid.begin());
    }
    return uid;
}

std::vector<Uid> tag_refs(const RawSet& set, uint16_t tag) {
    std::vector<Uid> refs;
    const TagValue* item = find_tag(set, tag);
    if (!item || item->value.size() < 8) {
        return refs;
    }
    const uint32_t count = read_u32(item->value.data());
    const uint32_t stride = read_u32(item->value.data() + 4);
    if (stride != 16 || item->value.size() < 8ULL + 16ULL * count) {
        return refs;
    }
    refs.reserve(count);
    for (uint32_t i = 0; i < count; ++i) {
        Uid uid{};
        const size_t offset = 8 + static_cast<size_t>(i) * 16;
        std::copy_n(item->value.begin() + static_cast<std::ptrdiff_t>(offset),
                    16, uid.begin());
        refs.push_back(uid);
    }
    return refs;
}

std::string utf16be_to_utf8(const std::vector<uint8_t>& bytes) {
    std::string result;
    for (size_t i = 0; i + 1 < bytes.size(); i += 2) {
        uint32_t cp = (static_cast<uint32_t>(bytes[i]) << 8) | bytes[i + 1];
        if ((cp & 0xfc00U) == 0xd800U && i + 3 < bytes.size()) {
            const uint32_t low =
                (static_cast<uint32_t>(bytes[i + 2]) << 8) | bytes[i + 3];
            if ((low & 0xfc00U) == 0xdc00U) {
                cp = 0x10000U + ((cp & 0x3ffU) << 10) + (low & 0x3ffU);
                i += 2;
            }
        }
        if (cp == 0) {
            break;
        }
        if (cp < 0x80) {
            result.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            result.push_back(static_cast<char>(0xc0 | (cp >> 6)));
            result.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        } else if (cp < 0x10000) {
            result.push_back(static_cast<char>(0xe0 | (cp >> 12)));
            result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
            result.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        } else {
            result.push_back(static_cast<char>(0xf0 | (cp >> 18)));
            result.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3f)));
            result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
            result.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        }
    }
    return result;
}

Partition parse_partition(const std::vector<uint8_t>& data, const Klv& klv) {
    Partition partition;
    partition.offset = klv.offset;
    partition.pack_length = klv.next_offset - klv.offset;
    partition.payload_offset = klv.value_offset;
    partition.end_offset = klv.next_offset;
    partition.status = klv.key[14];
    if (klv.key[13] == 2) {
        partition.type = PartitionType::Header;
    } else if (klv.key[13] == 3) {
        partition.type = PartitionType::Body;
    } else if (klv.key[13] == 4) {
        partition.type = PartitionType::Footer;
    }
    partition.closed =
        partition.type == PartitionType::Footer || !(klv.key[14] & 1);
    partition.complete = klv.key[14] > 2;
    if (klv.length < 84) {
        return partition;
    }
    const uint8_t* p = data.data() + klv.value_offset;
    partition.major = read_u16(p);
    partition.minor = read_u16(p + 2);
    partition.kag_size = read_u32(p + 4);
    partition.this_partition = read_u64(p + 8);
    partition.previous_partition = read_u64(p + 16);
    partition.footer_partition = read_u64(p + 24);
    partition.header_byte_count = read_u64(p + 32);
    partition.index_byte_count = read_u64(p + 40);
    partition.index_sid = read_u32(p + 48);
    partition.body_offset = read_u64(p + 52);
    partition.body_sid = read_u32(p + 60);
    std::copy_n(p + 64, 16, partition.operational_pattern.begin());
    const uint32_t count = read_u32(p + 80);
    if (84ULL + 16ULL * count <= klv.length) {
        partition.essence_containers.reserve(count);
        for (uint32_t i = 0; i < count; ++i) {
            Uid uid{};
            std::copy_n(p + 84 + static_cast<size_t>(i) * 16, 16,
                        uid.begin());
            partition.essence_containers.push_back(uid);
        }
    }
    return partition;
}

Descriptor parse_descriptor(const RawSet& set) {
    Descriptor descriptor;
    descriptor.klv_offset = set.offset;
    descriptor.instance_uid = set.instance_uid;
    descriptor.set_key = set.key;
    descriptor.linked_track_id = tag_u32(set, 0x3006);
    descriptor.duration = static_cast<int64_t>(tag_u64(set, 0x3002));
    descriptor.container_ul = tag_uid(set, 0x3004);
    descriptor.coding_ul = tag_uid(set, 0x3201);
    if (descriptor.coding_ul == Uid{}) {
        descriptor.coding_ul = tag_uid(set, 0x3d06);
    }
    descriptor.width = tag_u32(set, 0x3203);
    descriptor.height = tag_u32(set, 0x3202);
    descriptor.channels = tag_u32(set, 0x3d07);
    descriptor.bits_per_sample = tag_u32(set, 0x3d01);
    const TagValue* sample_rate = find_tag(set, 0x3d03);
    if (sample_rate && sample_rate->value.size() >= 8) {
        descriptor.sample_rate =
            Rational(read_u32(sample_rate->value.data()),
                     read_u32(sample_rate->value.data() + 4));
    }
    descriptor.sub_descriptor_refs = tag_refs(set, 0x3f01);
    const uint8_t type = set.key[14];
    if (type == 0x42 || type == 0x47 || type == 0x48 || type == 0x5e) {
        descriptor.kind = TrackKind::Audio;
    } else if (type == 0x28 || type == 0x29 || type == 0x51 ||
               type == 0x5b || type == 0x5c) {
        descriptor.kind = TrackKind::Video;
    } else if (type != 0x44) {
        if (find_tag(set, 0x3d03) || find_tag(set, 0x3d07)) {
            descriptor.kind = TrackKind::Audio;
        } else if (find_tag(set, 0x3202) || find_tag(set, 0x3203)) {
            descriptor.kind = TrackKind::Video;
        }
    }
    return descriptor;
}

Track parse_track(const RawSet& set) {
    Track track;
    track.klv_offset = set.offset;
    track.instance_uid = set.instance_uid;
    track.sequence_ref = tag_uid(set, 0x4803);
    track.track_id = tag_u32(set, 0x4801);
    const TagValue* number = find_tag(set, 0x4804);
    if (number && number->value.size() >= 4) {
        std::copy_n(number->value.begin(), 4, track.track_number.begin());
    }
    const TagValue* edit_rate = find_tag(set, 0x4b01);
    if (edit_rate && edit_rate->value.size() >= 8) {
        track.edit_rate =
            Rational(read_u32(edit_rate->value.data()),
                     read_u32(edit_rate->value.data() + 4));
    }
    track.origin = static_cast<int64_t>(tag_u64(set, 0x4b02));
    const TagValue* name = find_tag(set, 0x4802);
    if (name) {
        track.name = utf16be_to_utf8(name->value);
    }
    return track;
}

bool is_descriptor_type(uint8_t type) {
    static const std::set<uint8_t> types = {
        0x25, 0x27, 0x28, 0x29, 0x42, 0x44, 0x47, 0x48,
        0x51, 0x5b, 0x5c, 0x5e, 0x64};
    return types.count(type) != 0;
}

IndexSegment parse_index(const RawSet& set) {
    IndexSegment segment;
    segment.klv_offset = set.offset;
    segment.instance_uid = set.instance_uid;
    segment.edit_unit_byte_count = tag_u32(set, 0x3f05);
    segment.index_sid = tag_u32(set, 0x3f06);
    segment.body_sid = tag_u32(set, 0x3f07);
    const TagValue* rate = find_tag(set, 0x3f0b);
    if (rate && rate->value.size() >= 8) {
        segment.index_edit_rate =
            Rational(read_u32(rate->value.data()),
                     read_u32(rate->value.data() + 4));
    }
    segment.index_start_position = tag_u64(set, 0x3f0c);
    segment.index_duration = tag_u64(set, 0x3f0d);
    const TagValue* entries = find_tag(set, 0x3f0a);
    if (!entries || entries->value.size() < 8) {
        return segment;
    }
    const uint32_t count = read_u32(entries->value.data());
    const uint32_t entry_size = read_u32(entries->value.data() + 4);
    segment.entry_size = entry_size;
    if (entry_size < 11 ||
        entries->value.size() < 8ULL + static_cast<uint64_t>(count) * entry_size) {
        return segment;
    }
    segment.entries.reserve(count);
    for (uint32_t i = 0; i < count; ++i) {
        const uint8_t* p = entries->value.data() + 8 +
                           static_cast<size_t>(i) * entry_size;
        IndexEntry entry;
        entry.temporal_offset = static_cast<int8_t>(p[0]);
        entry.key_frame_offset = p[1];
        entry.flags = p[2];
        entry.stream_offset = read_u64(p + 3);
        segment.entries.push_back(entry);
    }
    return segment;
}

}  // namespace

std::string uid_hex(const Uid& uid) {
    static const char* digits = "0123456789abcdef";
    std::string out;
    out.reserve(32);
    for (uint8_t byte : uid) {
        out.push_back(digits[byte >> 4]);
        out.push_back(digits[byte & 0x0f]);
    }
    return out;
}

std::string ul_string(const Uid& uid) {
    const std::string hex = uid_hex(uid);
    std::string out;
    out.reserve(36);
    for (size_t i = 0; i < hex.size(); ++i) {
        if (i == 8 || i == 16 || i == 24) {
            out.push_back('.');
        }
        out.push_back(hex[i]);
    }
    return out;
}

std::string track_kind_name(TrackKind kind) {
    switch (kind) {
        case TrackKind::Video:
            return "video";
        case TrackKind::Audio:
            return "audio";
        case TrackKind::Data:
            return "data";
        case TrackKind::Timecode:
            return "timecode";
        default:
            return "unknown";
    }
}

std::string partition_type_name(PartitionType type) {
    switch (type) {
        case PartitionType::Header:
            return "header";
        case PartitionType::Body:
            return "body";
        case PartitionType::Footer:
            return "footer";
        default:
            return "unknown";
    }
}

TrackKind kind_from_data_definition(const Uid& ul) {
    const Uid timecode_prefix =
        make_uid({0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01, 0x01,
                  0x01, 0x03, 0x02, 0x01, 0x01});
    const Uid video_prefix =
        make_uid({0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01, 0x01,
                  0x01, 0x03, 0x02, 0x02, 0x01});
    const Uid audio_prefix =
        make_uid({0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01, 0x01,
                  0x01, 0x03, 0x02, 0x02, 0x02});
    if (std::equal(timecode_prefix.begin(), timecode_prefix.end(), ul.begin())) {
        return TrackKind::Timecode;
    }
    if (std::equal(video_prefix.begin(), video_prefix.end(), ul.begin())) {
        return TrackKind::Video;
    }
    if (std::equal(audio_prefix.begin(), audio_prefix.end(), ul.begin())) {
        return TrackKind::Audio;
    }
    return TrackKind::Unknown;
}

Document parse_mxf_file(const std::string& path) {
    std::string error;
    const std::vector<uint8_t> data = read_file(path, error);
    if (!error.empty()) {
        throw std::runtime_error(error);
    }
    if (data.size() < 32) {
        throw std::runtime_error("file is too small to be an MXF");
    }

    Document document;
    document.path = path;
    document.file_size = data.size();

    const size_t search_limit = std::min<size_t>(data.size(), 1U << 20U);
    for (size_t i = 0; i + 16 <= search_limit; ++i) {
        Uid key{};
        std::copy_n(data.begin() + static_cast<std::ptrdiff_t>(i), 16,
                    key.begin());
        if (is_partition_key(key)) {
            document.run_in = i;
            break;
        }
    }
    if (document.run_in == 0 && !is_partition_key(
            [&] {
                Uid key{};
                std::copy_n(data.begin(), 16, key.begin());
                return key;
            }())) {
        throw std::runtime_error("no MXF partition pack found");
    }

    std::vector<RawSet> raw_sets;
    size_t offset = document.run_in;
    while (offset + 17 <= data.size()) {
        Klv klv;
        if (!read_klv(data, offset, klv)) {
            document.warnings.push_back("invalid KLV at offset " +
                                        std::to_string(offset));
            break;
        }
        if (is_partition_key(klv.key)) {
            document.partitions.push_back(parse_partition(data, klv));
        } else if (is_primer_key(klv.key)) {
            if (klv.length >= 8) {
                const uint8_t* p = data.data() + klv.value_offset;
                const uint32_t count = read_u32(p);
                const uint32_t item_size = read_u32(p + 4);
                if (item_size >= 18 &&
                    8ULL + static_cast<uint64_t>(count) * item_size <=
                        klv.length) {
                    document.primer_count += count;
                }
            }
        } else if (is_index_key(klv.key)) {
            RawSet set = parse_local_set(data, klv);
            document.index_segments.push_back(parse_index(set));
        } else if (is_metadata_set_key(klv.key)) {
            raw_sets.push_back(parse_local_set(data, klv));
            ++document.metadata_set_count;
        } else if (is_essence_key(klv.key)) {
            EssenceElement element;
            element.klv_offset = klv.offset;
            element.value_offset = klv.value_offset;
            element.length = klv.length;
            element.key = klv.key;
            std::copy_n(klv.key.begin() + 12, 4,
                        element.track_number.begin());
            document.essence.push_back(element);
        } else if (is_fill_key(klv.key)) {
            ++document.fill_klv_count;
        } else {
            ++document.unknown_klv_count;
        }
        if (klv.next_offset <= offset) {
            break;
        }
        offset = klv.next_offset;
    }

    std::sort(document.partitions.begin(), document.partitions.end(),
              [](const Partition& a, const Partition& b) {
                  return a.offset < b.offset;
              });
    for (size_t i = 0; i < document.partitions.size(); ++i) {
        Partition& partition = document.partitions[i];
        partition.end_offset =
            i + 1 < document.partitions.size()
                ? document.partitions[i + 1].offset
                : data.size();
        if (partition.this_partition != partition.offset - document.run_in) {
            document.warnings.push_back(
                "partition at " + std::to_string(partition.offset) +
                " has ThisPartition=" +
                std::to_string(partition.this_partition) + ", expected " +
                std::to_string(partition.offset - document.run_in));
        }
    }
    for (auto& element : document.essence) {
        for (size_t i = 0; i < document.partitions.size(); ++i) {
            const Partition& partition = document.partitions[i];
            if (element.klv_offset >= partition.offset &&
                element.klv_offset < partition.end_offset) {
                element.partition_index = static_cast<int>(i);
                if (partition.first_essence_offset == 0) {
                    document.partitions[i].first_essence_offset =
                        element.klv_offset;
                }
            }
        }
    }

    std::unordered_map<std::string, const RawSet*> set_by_uid;
    for (const auto& set : raw_sets) {
        if (set.instance_uid != Uid{}) {
            set_by_uid[uid_hex(set.instance_uid)] = &set;
        }
    }

    std::unordered_map<std::string, bool> material_tracks;
    for (const auto& set : raw_sets) {
        if (is_metadata_set_key(set.key) && set.key[14] == 0x36) {
            for (const Uid& ref : tag_refs(set, 0x4403)) {
                material_tracks[uid_hex(ref)] = true;
            }
        }
    }

    for (const auto& set : raw_sets) {
        if (!is_metadata_set_key(set.key)) {
            continue;
        }
        const uint8_t type = set.key[14];
        if (type == 0x14) {
            TimecodeInfo info;
            info.present = true;
            info.instance_uid = set.instance_uid;
            info.start_frame = static_cast<int64_t>(tag_u64(set, 0x1501));
            info.nominal_base = tag_u16(set, 0x1502, 0);
            const TagValue* drop = find_tag(set, 0x1503);
            info.drop = drop && !drop->value.empty() && drop->value[0] != 0;
            document.timecodes.push_back(info);
        }
    }

    for (const auto& set : raw_sets) {
        if (!is_metadata_set_key(set.key) ||
            !is_descriptor_type(set.key[14]) || set.key[14] == 0x44) {
            continue;
        }
        document.descriptors.push_back(parse_descriptor(set));
    }

    for (const auto& set : raw_sets) {
        if (!is_metadata_set_key(set.key)) {
            continue;
        }
        const uint8_t type = set.key[14];
        if (type != 0x3a && type != 0x3b) {
            continue;
        }
        Track track = parse_track(set);
        track.material = material_tracks.count(uid_hex(track.instance_uid)) != 0;
        Uid data_definition{};
        const auto sequence_it = set_by_uid.find(uid_hex(track.sequence_ref));
        if (sequence_it != set_by_uid.end()) {
            const RawSet& sequence = *sequence_it->second;
            data_definition = tag_uid(sequence, 0x0201);
            track.duration =
                static_cast<int64_t>(tag_u64(sequence, 0x0202));
        }
        const TrackKind definition_kind =
            kind_from_data_definition(data_definition);
        track.kind = definition_kind;
        if (!track.material) {
            for (size_t i = 0; i < document.descriptors.size(); ++i) {
                const Descriptor& descriptor = document.descriptors[i];
                if (descriptor.linked_track_id != track.track_id) {
                    continue;
                }
                track.descriptor_index = static_cast<int>(i);
                if (descriptor.kind != TrackKind::Unknown) {
                    track.kind = descriptor.kind;
                }
                break;
            }
        }
        if (track.duration == 0 && track.descriptor_index >= 0) {
            track.duration =
                document.descriptors[static_cast<size_t>(
                    track.descriptor_index)].duration;
        }
        document.tracks.push_back(std::move(track));
    }

    verify_index_tables(document);
    return document;
}

}  // namespace mxf
