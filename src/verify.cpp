#include "mxf/mxf.hpp"

#include <algorithm>
#include <map>
#include <sstream>
#include <unordered_map>

namespace mxf {

const Partition* Document::partition_for_stream_offset(
    uint32_t body_sid, uint64_t stream_offset) const {
    const Partition* best = nullptr;
    for (const Partition& partition : partitions) {
        if (partition.body_sid != body_sid ||
            partition.first_essence_offset == 0 ||
            partition.body_offset > stream_offset) {
            continue;
        }
        if (!best || partition.body_offset > best->body_offset ||
            (partition.body_offset == best->body_offset &&
             partition.offset > best->offset)) {
            best = &partition;
        }
    }
    return best;
}

int Document::find_track_by_number(
    const std::array<uint8_t, 4>& number) const {
    for (size_t i = 0; i < tracks.size(); ++i) {
        if (tracks[i].track_number == number) {
            return static_cast<int>(i);
        }
    }
    return -1;
}

namespace {

std::string decimal_u64(uint64_t value) {
    return std::to_string(value);
}

std::string offset_text(size_t value) {
    std::ostringstream stream;
    stream << "0x" << std::hex << value;
    return stream.str();
}

std::string number_hex(const std::array<uint8_t, 4>& number) {
    static const char* digits = "0123456789abcdef";
    std::string result;
    result.reserve(8);
    for (uint8_t byte : number) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0x0f]);
    }
    return result;
}

std::vector<size_t> elements_for_number(
    const Document& document, const std::array<uint8_t, 4>& number) {
    std::vector<size_t> result;
    for (size_t i = 0; i < document.essence.size(); ++i) {
        if (document.essence[i].track_number == number) {
            result.push_back(i);
        }
    }
    std::sort(result.begin(), result.end(), [&](size_t a, size_t b) {
        return document.essence[a].klv_offset < document.essence[b].klv_offset;
    });
    return result;
}

}  // namespace

void verify_index_tables(Document& document) {
    document.index_verifications.clear();
    std::unordered_map<size_t, size_t> by_key_offset;
    std::unordered_map<size_t, size_t> by_value_offset;
    for (size_t i = 0; i < document.essence.size(); ++i) {
        by_key_offset[document.essence[i].klv_offset] = i;
        by_value_offset[document.essence[i].value_offset] = i;
    }

    for (size_t segment_index = 0;
         segment_index < document.index_segments.size(); ++segment_index) {
        const IndexSegment& segment = document.index_segments[segment_index];
        IndexVerification check;
        check.segment_index = segment_index;

        auto locate = [&](uint64_t stream_offset, size_t& element_index,
                          bool& clip_wrapped) {
            const Partition* partition = document.partition_for_stream_offset(
                segment.body_sid, stream_offset);
            if (!partition) {
                return false;
            }
            const size_t mapped =
                partition->first_essence_offset +
                static_cast<size_t>(stream_offset - partition->body_offset);
            auto key_it = by_key_offset.find(mapped);
            if (key_it != by_key_offset.end()) {
                element_index = key_it->second;
                clip_wrapped = false;
                return true;
            }
            auto value_it = by_value_offset.find(mapped);
            if (value_it != by_value_offset.end()) {
                element_index = value_it->second;
                clip_wrapped = true;
                return true;
            }
            return false;
        };

        bool saw_clip_wrapped = false;
        if (!segment.entries.empty()) {
            size_t first_element = 0;
            bool first_clip = false;
            if (locate(segment.entries.front().stream_offset, first_element,
                       first_clip)) {
                check.track_number =
                    document.essence[first_element].track_number;
                check.track_index =
                    document.find_track_by_number(check.track_number);
                saw_clip_wrapped = first_clip;
            } else {
                check.failures.push_back(
                    "first StreamOffset " +
                    decimal_u64(segment.entries.front().stream_offset) +
                    " does not resolve to an essence element");
            }
        }

        const std::vector<size_t> track_elements =
            elements_for_number(document, check.track_number);

        for (size_t i = 0; i < segment.entries.size(); ++i) {
            const IndexEntry& entry = segment.entries[i];
            ++check.entries_checked;
            size_t element_index = 0;
            bool clip_wrapped = false;
            if (!locate(entry.stream_offset, element_index, clip_wrapped)) {
                if (check.failures.size() < 12) {
                    check.failures.push_back(
                        "entry " + std::to_string(i) + " StreamOffset " +
                        decimal_u64(entry.stream_offset) +
                        " resolves to no essence KLV");
                }
                continue;
            }
            saw_clip_wrapped = saw_clip_wrapped || clip_wrapped;
            if (check.track_index < 0) {
                check.track_number =
                    document.essence[element_index].track_number;
                check.track_index =
                    document.find_track_by_number(check.track_number);
            }
            const EssenceElement& actual = document.essence[element_index];
            if (actual.track_number != check.track_number) {
                if (check.failures.size() < 12) {
                    check.failures.push_back(
                        "entry " + std::to_string(i) +
                        " points to track " +
                        number_hex(actual.track_number) + " instead of " +
                        number_hex(check.track_number));
                }
                continue;
            }

            const uint64_t edit_unit =
                segment.index_start_position + static_cast<uint64_t>(i);
            if (edit_unit >= track_elements.size()) {
                if (check.failures.size() < 12) {
                    check.failures.push_back(
                        "entry " + std::to_string(i) +
                        " exceeds actual track element count " +
                        std::to_string(track_elements.size()));
                }
                continue;
            }
            const EssenceElement& expected =
                document.essence[track_elements[static_cast<size_t>(edit_unit)]];
            const bool position_matches =
                clip_wrapped ? actual.value_offset == expected.value_offset
                             : actual.klv_offset == expected.klv_offset;
            if (!position_matches) {
                if (check.failures.size() < 12) {
                    check.failures.push_back(
                        "entry " + std::to_string(i) + " maps to " +
                        offset_text(actual.klv_offset) + ", actual edit unit " +
                        std::to_string(edit_unit) + " is at " +
                        offset_text(expected.klv_offset));
                }
                continue;
            }
            ++check.entries_matched;
        }

        if (segment.entries.empty() && segment.edit_unit_byte_count > 0 &&
            segment.index_duration > 0) {
            const Partition* partition = document.partition_for_stream_offset(
                segment.body_sid, 0);
            if (partition) {
                check.track_index = -1;
                for (uint64_t i = 0; i < segment.index_duration; ++i) {
                    const size_t mapped =
                        partition->first_essence_offset +
                        static_cast<size_t>(segment.edit_unit_byte_count * i);
                    auto found = by_key_offset.find(mapped);
                    ++check.entries_checked;
                    if (found == by_key_offset.end()) {
                        if (check.failures.size() < 12) {
                            check.failures.push_back(
                                "fixed-size edit unit " + std::to_string(i) +
                                " expected at " + offset_text(mapped) +
                                " but no essence KLV starts there");
                        }
                        continue;
                    }
                    const EssenceElement& actual =
                        document.essence[found->second];
                    if (i == 0) {
                        check.track_number = actual.track_number;
                        check.track_index =
                            document.find_track_by_number(check.track_number);
                    } else if (actual.track_number != check.track_number) {
                        if (check.failures.size() < 12) {
                            check.failures.push_back(
                                "fixed-size edit unit " + std::to_string(i) +
                                " crosses into another track");
                        }
                        continue;
                    }
                    ++check.entries_matched;
                }
            }
        }

        if (!segment.entries.empty() && segment.index_duration > 0) {
            const uint64_t count = segment.entries.size();
            const uint64_t duration = segment.index_duration;
            if (count != duration && count != duration + 1 &&
                count != duration * 2 + 1) {
                check.failures.push_back(
                    "IndexDuration " + std::to_string(duration) +
                    " does not match IndexEntryCount " + std::to_string(count));
            }
        }
        if (saw_clip_wrapped) {
            document.warnings.push_back(
                "index segment " + std::to_string(segment_index) +
                " refers to clip-wrapped essence payload offsets");
        }
        check.pass = check.failures.empty() && check.entries_checked > 0 &&
                     check.entries_checked == check.entries_matched;
        document.index_verifications.push_back(std::move(check));
    }
}

}  // namespace mxf
