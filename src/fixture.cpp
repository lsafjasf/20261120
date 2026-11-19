#include "mxf/fixture.hpp"

#include "mxf/mxf.hpp"

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <vector>

namespace mxf {

namespace {

using Bytes = std::vector<uint8_t>;

void put_u8(Bytes& out, uint8_t value) {
    out.push_back(value);
}

void put_u16(Bytes& out, uint16_t value) {
    out.push_back(static_cast<uint8_t>(value >> 8));
    out.push_back(static_cast<uint8_t>(value));
}

void put_u16le(Bytes& out, uint16_t value) {
    out.push_back(static_cast<uint8_t>(value));
    out.push_back(static_cast<uint8_t>(value >> 8));
}

void put_u32(Bytes& out, uint32_t value) {
    out.push_back(static_cast<uint8_t>(value >> 24));
    out.push_back(static_cast<uint8_t>(value >> 16));
    out.push_back(static_cast<uint8_t>(value >> 8));
    out.push_back(static_cast<uint8_t>(value));
}

void put_u64(Bytes& out, uint64_t value) {
    put_u32(out, static_cast<uint32_t>(value >> 32));
    put_u32(out, static_cast<uint32_t>(value));
}

void put_uid(Bytes& out, const Uid& uid) {
    out.insert(out.end(), uid.begin(), uid.end());
}

void put_ber_length(Bytes& out, size_t length) {
    if (length < 0x80) {
        put_u8(out, static_cast<uint8_t>(length));
        return;
    }
    uint8_t bytes[8];
    int count = 0;
    size_t value = length;
    while (value != 0) {
        bytes[count++] = static_cast<uint8_t>(value);
        value >>= 8;
    }
    put_u8(out, static_cast<uint8_t>(0x80 | count));
    while (count > 0) {
        put_u8(out, bytes[--count]);
    }
}

void put_klv(Bytes& out, const Uid& key, const Bytes& payload) {
    put_uid(out, key);
    put_ber_length(out, payload.size());
    out.insert(out.end(), payload.begin(), payload.end());
}

Uid item_uid(uint64_t id) {
    Uid uid{0x06, 0x0a, 0x2b, 0x34, 0x01, 0x01, 0x01, 0x05,
            0x01, 0x01, 0x0d, 0x00, 0x13, 0xe3, 0xac, 0xdf};
    for (int i = 0; i < 8; ++i) {
        uid[15 - i] = static_cast<uint8_t>(id >> (i * 8));
    }
    return uid;
}

Uid metadata_key(uint8_t type) {
    return Uid{0x06, 0x0e, 0x2b, 0x34, 0x02, 0x53, 0x01, 0x01,
               0x0d, 0x01, 0x01, 0x01, 0x01, 0x01, type, 0x00};
}

Uid partition_key(uint8_t type, uint8_t status) {
    return Uid{0x06, 0x0e, 0x2b, 0x34, 0x02, 0x05, 0x01, 0x01,
               0x0d, 0x01, 0x02, 0x01, 0x01, type, status, 0x00};
}

void put_tag(Bytes& payload, uint16_t tag, const Bytes& value) {
    put_u16(payload, tag);
    put_u16(payload, static_cast<uint16_t>(value.size()));
    payload.insert(payload.end(), value.begin(), value.end());
}

void put_tag_u8(Bytes& payload, uint16_t tag, uint8_t value) {
    put_tag(payload, tag, Bytes{value});
}

void put_tag_u16(Bytes& payload, uint16_t tag, uint16_t value) {
    Bytes bytes;
    put_u16(bytes, value);
    put_tag(payload, tag, bytes);
}

void put_tag_u32(Bytes& payload, uint16_t tag, uint32_t value) {
    Bytes bytes;
    put_u32(bytes, value);
    put_tag(payload, tag, bytes);
}

void put_tag_u64(Bytes& payload, uint16_t tag, uint64_t value) {
    Bytes bytes;
    put_u64(bytes, value);
    put_tag(payload, tag, bytes);
}

void put_tag_uid(Bytes& payload, uint16_t tag, const Uid& uid) {
    Bytes bytes;
    put_uid(bytes, uid);
    put_tag(payload, tag, bytes);
}

void put_tag_rate(Bytes& payload, uint16_t tag, const Rational& rate) {
    Bytes bytes;
    put_u32(bytes, static_cast<uint32_t>(rate.num));
    put_u32(bytes, static_cast<uint32_t>(rate.den));
    put_tag(payload, tag, bytes);
}

void put_tag_refs(Bytes& payload, uint16_t tag,
                  const std::vector<Uid>& refs) {
    Bytes bytes;
    put_u32(bytes, static_cast<uint32_t>(refs.size()));
    put_u32(bytes, 16);
    for (const Uid& ref : refs) {
        put_uid(bytes, ref);
    }
    put_tag(payload, tag, bytes);
}

Bytes local_set(uint8_t type, const Uid& instance,
                const std::vector<uint8_t>& body) {
    Bytes payload;
    put_tag_uid(payload, 0x3c0a, instance);
    payload.insert(payload.end(), body.begin(), body.end());
    Bytes klv;
    put_klv(klv, metadata_key(type), payload);
    return klv;
}

Uid essence_key(uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
    return Uid{0x06, 0x0e, 0x2b, 0x34, 0x01, 0x02, 0x01, 0x01,
               0x0d, 0x01, 0x03, 0x01, a, b, c, d};
}

Bytes partition_payload(uint64_t this_partition, uint64_t previous,
                        uint64_t footer, uint64_t header_bytes,
                        uint64_t index_bytes, uint32_t index_sid,
                        uint64_t body_offset, uint32_t body_sid,
                        const std::vector<Uid>& containers) {
    Bytes payload;
    put_u16(payload, 1);
    put_u16(payload, 3);
    put_u32(payload, 1);
    put_u64(payload, this_partition);
    put_u64(payload, previous);
    put_u64(payload, footer);
    put_u64(payload, header_bytes);
    put_u64(payload, index_bytes);
    put_u32(payload, index_sid);
    put_u64(payload, body_offset);
    put_u32(payload, body_sid);
    put_uid(payload, Uid{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01, 0x01,
                         0x0d, 0x01, 0x02, 0x01, 0x01, 0x01, 0x09, 0x00});
    put_u32(payload, static_cast<uint32_t>(containers.size()));
    for (const Uid& container : containers) {
        put_uid(payload, container);
    }
    return payload;
}

Bytes make_partition(uint8_t type, const Bytes& payload) {
    Bytes out;
    put_klv(out, partition_key(type, 0x04), payload);
    return out;
}

size_t klv_payload_offset(size_t start, size_t payload_size) {
    if (payload_size < 0x80) {
        return start + 17;
    }
    size_t value = payload_size;
    size_t count = 0;
    while (value != 0) {
        ++count;
        value >>= 8;
    }
    return start + 16 + 1 + count;
}

void patch_u64(Bytes& data, size_t offset, uint64_t value) {
    if (offset + 8 > data.size()) {
        return;
    }
    for (int i = 0; i < 8; ++i) {
        data[offset + static_cast<size_t>(i)] =
            static_cast<uint8_t>(value >> (56 - i * 8));
    }
}

Bytes utf16be(const std::string& text) {
    Bytes out;
    for (char ch : text) {
        put_u16(out, static_cast<uint8_t>(ch));
    }
    return out;
}

}  // namespace

bool write_sample_mxf(const std::string& path, const FixtureOptions& options,
                      std::string& error) {
    if (options.frames <= 0 || !options.edit_rate.valid() ||
        options.sample_rate <= 0) {
        error = "invalid fixture options";
        return false;
    }

    const Uid material_package_uid = item_uid(3);
    const Uid source_package_uid = item_uid(4);
    const Uid mat_video_track_uid = item_uid(10);
    const Uid mat_video_sequence_uid = item_uid(11);
    const Uid mat_video_clip_uid = item_uid(12);
    const Uid mat_audio_track_uid = item_uid(13);
    const Uid mat_audio_sequence_uid = item_uid(14);
    const Uid mat_audio_clip_uid = item_uid(15);
    const Uid mat_tc_track_uid = item_uid(16);
    const Uid mat_tc_sequence_uid = item_uid(17);
    const Uid tc_component_uid = item_uid(18);
    const Uid src_video_track_uid = item_uid(20);
    const Uid src_video_sequence_uid = item_uid(21);
    const Uid src_video_clip_uid = item_uid(22);
    const Uid src_audio_track_uid = item_uid(23);
    const Uid src_audio_sequence_uid = item_uid(24);
    const Uid src_audio_clip_uid = item_uid(25);
    const Uid video_descriptor_uid = item_uid(30);
    const Uid audio_descriptor_uid = item_uid(31);
    const Uid multiple_descriptor_uid = item_uid(32);
    const Uid essence_data_uid = item_uid(33);

    const Uid video_definition{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01,
                               0x01, 0x01, 0x03, 0x02, 0x02, 0x01, 0x00,
                               0x00, 0x00};
    const Uid audio_definition{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01,
                               0x01, 0x01, 0x03, 0x02, 0x02, 0x02, 0x00,
                               0x00, 0x00};
    const Uid timecode_definition{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01,
                                  0x01, 0x01, 0x01, 0x03, 0x02, 0x01,
                                  0x01, 0x00, 0x00, 0x00};
    const Uid container_ul{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01,
                           0x01, 0x0d, 0x01, 0x03, 0x01, 0x02, 0x06,
                           0x03, 0x00};
    const Uid video_codec_ul{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01,
                             0x03, 0x04, 0x01, 0x02, 0x02, 0x01, 0x01,
                             0x11, 0x00};
    const Uid audio_codec_ul{0x06, 0x0e, 0x2b, 0x34, 0x04, 0x01, 0x01,
                             0x01, 0x04, 0x02, 0x02, 0x01, 0x00, 0x00,
                             0x00, 0x00};

    Bytes package_umid;
    put_uid(package_umid, item_uid(100));
    put_uid(package_umid, item_uid(101));

    auto package_set = [&](uint8_t type, const Uid& instance,
                           const std::vector<Uid>& tracks,
                           const Uid& descriptor) {
        Bytes body;
        put_tag(body, 0x4401, package_umid);
        put_tag(body, 0x4402, utf16be(type == 0x36 ? "material" : "source"));
        put_tag_refs(body, 0x4403, tracks);
        if (descriptor != Uid{}) {
            put_tag_uid(body, 0x4701, descriptor);
        }
        return local_set(type, instance, body);
    };

    auto track_set = [&](const Uid& instance, uint32_t track_id,
                         const std::array<uint8_t, 4>& number,
                         const Uid& sequence, const std::string& name) {
        Bytes body;
        put_tag_u32(body, 0x4801, track_id);
        Bytes number_bytes(number.begin(), number.end());
        put_tag(body, 0x4804, number_bytes);
        put_tag_rate(body, 0x4b01, options.edit_rate);
        put_tag_uid(body, 0x4803, sequence);
        put_tag(body, 0x4802, utf16be(name));
        return local_set(0x3b, instance, body);
    };

    auto sequence_set = [&](const Uid& instance, const Uid& definition,
                            const Uid& component) {
        Bytes body;
        put_tag_uid(body, 0x0201, definition);
        put_tag_u64(body, 0x0202, static_cast<uint64_t>(options.frames));
        put_tag_refs(body, 0x1001, {component});
        return local_set(0x0f, instance, body);
    };

    auto clip_set = [&](const Uid& instance, const Uid& definition,
                        uint32_t source_track_id) {
        Bytes body;
        put_tag_uid(body, 0x0201, definition);
        put_tag_u64(body, 0x0202, static_cast<uint64_t>(options.frames));
        put_tag_u64(body, 0x1201, 0);
        put_tag(body, 0x1101, package_umid);
        put_tag_u32(body, 0x1102, source_track_id);
        return local_set(0x11, instance, body);
    };

    Bytes metadata;
    auto append = [&](Bytes klv) {
        metadata.insert(metadata.end(), klv.begin(), klv.end());
    };

    append(package_set(0x36, material_package_uid,
                       {mat_video_track_uid, mat_audio_track_uid,
                        mat_tc_track_uid},
                       Uid{}));
    append(package_set(0x37, source_package_uid,
                       {src_video_track_uid, src_audio_track_uid},
                       multiple_descriptor_uid));

    append(track_set(mat_video_track_uid, 1, {0, 0, 0, 0},
                     mat_video_sequence_uid, "material picture"));
    append(track_set(mat_audio_track_uid, 3, {0, 0, 0, 0},
                     mat_audio_sequence_uid, "material sound"));
    append(track_set(mat_tc_track_uid, 4, {0, 0, 0, 0},
                     mat_tc_sequence_uid, "material timecode"));
    append(track_set(src_video_track_uid, 2, {0x15, 0x01, 0x05, 0x00},
                     src_video_sequence_uid, "source picture"));
    append(track_set(src_audio_track_uid, 3, {0x16, 0x01, 0x03, 0x00},
                     src_audio_sequence_uid, "source sound"));

    append(sequence_set(mat_video_sequence_uid, video_definition,
                        mat_video_clip_uid));
    append(sequence_set(mat_audio_sequence_uid, audio_definition,
                        mat_audio_clip_uid));
    append(sequence_set(mat_tc_sequence_uid, timecode_definition,
                        tc_component_uid));
    append(sequence_set(src_video_sequence_uid, video_definition,
                        src_video_clip_uid));
    append(sequence_set(src_audio_sequence_uid, audio_definition,
                        src_audio_clip_uid));

    append(clip_set(mat_video_clip_uid, video_definition, 2));
    append(clip_set(mat_audio_clip_uid, audio_definition, 3));
    append(clip_set(src_video_clip_uid, video_definition, 0));
    append(clip_set(src_audio_clip_uid, audio_definition, 0));

    {
        Bytes body;
        put_tag_uid(body, 0x0201, timecode_definition);
        put_tag_u64(body, 0x0202, static_cast<uint64_t>(options.frames));
        put_tag_u64(body, 0x1501, 0);
        put_tag_u16(body, 0x1502,
                    static_cast<uint16_t>((options.edit_rate.num +
                                           options.edit_rate.den / 2) /
                                          options.edit_rate.den));
        put_tag_u8(body, 0x1503, options.drop_frame_timecode ? 1 : 0);
        append(local_set(0x14, tc_component_uid, body));
    }

    {
        Bytes body;
        put_tag_u32(body, 0x3006, 2);
        put_tag_u64(body, 0x3002, static_cast<uint64_t>(options.frames));
        put_tag_uid(body, 0x3004, container_ul);
        put_tag_uid(body, 0x3005, video_codec_ul);
        put_tag_uid(body, 0x3201, video_codec_ul);
        put_tag_u32(body, 0x3203, 160);
        put_tag_u32(body, 0x3202, 120);
        put_tag_u8(body, 0x320c, 0);
        Bytes aspect;
        put_u32(aspect, 4);
        put_u32(aspect, 3);
        put_tag(body, 0x320e, aspect);
        put_tag_u32(body, 0x3301, 8);
        append(local_set(0x51, video_descriptor_uid, body));
    }
    {
        Bytes body;
        put_tag_u32(body, 0x3006, 3);
        put_tag_u64(body, 0x3002, static_cast<uint64_t>(options.frames));
        put_tag_uid(body, 0x3004, container_ul);
        put_tag_uid(body, 0x3005, audio_codec_ul);
        put_tag_rate(body, 0x3d03,
                     Rational(options.sample_rate, 1));
        put_tag_u32(body, 0x3d07, 1);
        put_tag_u32(body, 0x3d01, 16);
        append(local_set(0x48, audio_descriptor_uid, body));
    }
    {
        Bytes body;
        put_tag_refs(body, 0x3f01,
                     {video_descriptor_uid, audio_descriptor_uid});
        append(local_set(0x44, multiple_descriptor_uid, body));
    }
    {
        Bytes body;
        put_tag(body, 0x2701, package_umid);
        put_tag_u32(body, 0x3f06, 2);
        put_tag_u32(body, 0x3f07, 1);
        append(local_set(0x23, essence_data_uid, body));
    }

    Bytes primer_payload;
    put_u32(primer_payload, 1);
    put_u32(primer_payload, 18);
    put_u16(primer_payload, 0x3c0a);
    put_uid(primer_payload, item_uid(1000));
    Bytes primer;
    put_klv(primer, Uid{0x06, 0x0e, 0x2b, 0x34, 0x02, 0x05, 0x01, 0x01,
                        0x0d, 0x01, 0x02, 0x01, 0x01, 0x05, 0x01, 0x00},
            primer_payload);

    const size_t header_metadata_bytes = primer.size() + metadata.size();
    Bytes header_payload = partition_payload(
        0, 0, 0, header_metadata_bytes, 0, 1, 0, 0, {container_ul});
    Bytes file = make_partition(0x02, header_payload);
    const size_t header_payload_offset =
        klv_payload_offset(0, header_payload.size());
    file.insert(file.end(), primer.begin(), primer.end());
    file.insert(file.end(), metadata.begin(), metadata.end());

    const size_t body_offset = file.size();
    Bytes body_payload = partition_payload(
        body_offset, 0, 0, 0, 0, 1, 0, 1, {container_ul});
    Bytes body_partition = make_partition(0x03, body_payload);
    const size_t body_payload_offset =
        klv_payload_offset(body_offset, body_payload.size());
    file.insert(file.end(), body_partition.begin(), body_partition.end());

    const size_t body_essence_start = file.size();
    std::vector<uint64_t> video_offsets;
    std::vector<uint64_t> audio_offsets;
    video_offsets.reserve(static_cast<size_t>(options.frames));
    audio_offsets.reserve(static_cast<size_t>(options.frames));

    const Uid video_key = essence_key(0x15, 0x01, 0x05, 0x00);
    const Uid audio_key = essence_key(0x16, 0x01, 0x03, 0x00);
    for (int frame = 0; frame < options.frames; ++frame) {
        video_offsets.push_back(file.size() - body_essence_start);
        Bytes video_payload(96, 0);
        const char magic[6] = {'M', 'X', 'V', 'I', 'D', '1'};
        std::copy(std::begin(magic), std::end(magic), video_payload.begin());
        for (size_t i = 6; i < video_payload.size(); ++i) {
            video_payload[i] = static_cast<uint8_t>(
                (frame * 37 + static_cast<int>(i) * 11) & 0xff);
        }
        Bytes video_klv;
        put_klv(video_klv, video_key, video_payload);
        file.insert(file.end(), video_klv.begin(), video_klv.end());

        audio_offsets.push_back(file.size() - body_essence_start);
        const int64_t sample_begin =
            scale_round(frame, options.edit_rate,
                        Rational(options.sample_rate, 1));
        const int64_t sample_end =
            scale_round(frame + 1, options.edit_rate,
                        Rational(options.sample_rate, 1));
        Bytes audio_payload;
        audio_payload.reserve(
            static_cast<size_t>((sample_end - sample_begin) * 2));
        for (int64_t sample = sample_begin; sample < sample_end; ++sample) {
            const auto value = static_cast<int16_t>(
                (1000 + sample * 7 + frame * 3) & 0x7fff);
            put_u16le(audio_payload, static_cast<uint16_t>(value));
        }
        Bytes audio_klv;
        put_klv(audio_klv, audio_key, audio_payload);
        file.insert(file.end(), audio_klv.begin(), audio_klv.end());
    }

    auto make_index = [&](uint32_t index_sid,
                          const std::vector<uint64_t>& offsets,
                          uint64_t instance_id) {
        Bytes entries;
        put_u32(entries, static_cast<uint32_t>(offsets.size()));
        put_u32(entries, 11);
        for (uint64_t offset : offsets) {
            put_u8(entries, 0);
            put_u8(entries, 0);
            put_u8(entries, 0);
            put_u64(entries, offset);
        }
        Bytes payload;
        put_tag_u32(payload, 0x3f05, 0);
        put_tag_u32(payload, 0x3f06, index_sid);
        put_tag_u32(payload, 0x3f07, 1);
        put_tag_rate(payload, 0x3f0b, options.edit_rate);
        put_tag_u64(payload, 0x3f0c, 0);
        put_tag_u64(payload, 0x3f0d,
                    static_cast<uint64_t>(options.frames));
        put_tag(payload, 0x3f0a, entries);
        Bytes payload_with_uid;
        put_tag_uid(payload_with_uid, 0x3c0a, item_uid(instance_id));
        payload_with_uid.insert(payload_with_uid.end(), payload.begin(),
                                payload.end());
        Bytes klv;
        put_klv(klv,
                Uid{0x06, 0x0e, 0x2b, 0x34, 0x02, 0x53, 0x01, 0x01,
                    0x0d, 0x01, 0x02, 0x01, 0x01, 0x10, 0x01, 0x00},
                payload_with_uid);
        return klv;
    };
    const Bytes video_index = make_index(1, video_offsets, 200);
    const Bytes audio_index = make_index(2, audio_offsets, 201);
    const size_t index_bytes = video_index.size() + audio_index.size();

    const size_t footer_offset = file.size();
    Bytes footer_payload = partition_payload(
        footer_offset, body_offset, footer_offset, 0, index_bytes, 2, 0,
        0, {container_ul});
    Bytes footer_partition = make_partition(0x04, footer_payload);
    file.insert(file.end(), footer_partition.begin(), footer_partition.end());
    file.insert(file.end(), video_index.begin(), video_index.end());
    file.insert(file.end(), audio_index.begin(), audio_index.end());

    patch_u64(file, header_payload_offset + 24, footer_offset);
    patch_u64(file, header_payload_offset + 40, index_bytes);
    patch_u64(file, body_payload_offset + 24, footer_offset);
    patch_u64(file, body_payload_offset + 40, index_bytes);

    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output) {
        error = "cannot create " + path;
        return false;
    }
    output.write(reinterpret_cast<const char*>(file.data()),
                 static_cast<std::streamsize>(file.size()));
    if (!output) {
        error = "cannot write " + path;
        return false;
    }
    error.clear();
    return true;
}

}  // namespace mxf
