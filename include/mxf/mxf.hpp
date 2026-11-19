#pragma once

#include "mxf/rational.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace mxf {

using Uid = std::array<uint8_t, 16>;

std::string uid_hex(const Uid& uid);
std::string ul_string(const Uid& uid);

enum class PartitionType { Header, Body, Footer, Unknown };
enum class TrackKind { Video, Audio, Data, Timecode, Unknown };
enum class Wrapping { Unknown, Frame, Clip };

struct Partition {
    size_t offset = 0;
    size_t pack_length = 0;
    size_t payload_offset = 0;
    PartitionType type = PartitionType::Unknown;
    int status = 0;
    bool closed = false;
    bool complete = false;
    uint16_t major = 0;
    uint16_t minor = 0;
    uint32_t kag_size = 0;
    uint64_t this_partition = 0;
    uint64_t previous_partition = 0;
    uint64_t footer_partition = 0;
    uint64_t header_byte_count = 0;
    uint64_t index_byte_count = 0;
    uint32_t index_sid = 0;
    uint64_t body_offset = 0;
    uint32_t body_sid = 0;
    Uid operational_pattern{};
    std::vector<Uid> essence_containers;
    size_t first_essence_offset = 0;
    size_t end_offset = 0;
};

struct Descriptor {
    size_t klv_offset = 0;
    Uid instance_uid{};
    Uid set_key{};
    TrackKind kind = TrackKind::Unknown;
    uint32_t linked_track_id = 0;
    int64_t duration = 0;
    Uid container_ul{};
    Uid coding_ul{};
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t channels = 0;
    uint32_t bits_per_sample = 0;
    Rational sample_rate{0, 1};
    std::vector<Uid> sub_descriptor_refs;
};

struct Track {
    size_t klv_offset = 0;
    Uid instance_uid{};
    Uid sequence_ref{};
    uint32_t track_id = 0;
    std::array<uint8_t, 4> track_number{};
    Rational edit_rate{0, 1};
    int64_t origin = 0;
    int64_t duration = 0;
    std::string name;
    bool material = false;
    TrackKind kind = TrackKind::Unknown;
    int descriptor_index = -1;
    int index_sid = 0;
    int body_sid = 0;
};

struct TimecodeInfo {
    bool present = false;
    int64_t start_frame = 0;
    int nominal_base = 30;
    bool drop = false;
    Uid instance_uid{};
};

struct IndexEntry {
    int8_t temporal_offset = 0;
    uint8_t key_frame_offset = 0;
    uint8_t flags = 0;
    uint64_t stream_offset = 0;
};

struct IndexSegment {
    size_t klv_offset = 0;
    Uid instance_uid{};
    uint32_t edit_unit_byte_count = 0;
    uint32_t index_sid = 0;
    uint32_t body_sid = 0;
    Rational index_edit_rate{0, 1};
    uint64_t index_start_position = 0;
    uint64_t index_duration = 0;
    uint32_t entry_size = 0;
    std::vector<IndexEntry> entries;
};

struct EssenceElement {
    size_t klv_offset = 0;
    size_t value_offset = 0;
    uint64_t length = 0;
    Uid key{};
    std::array<uint8_t, 4> track_number{};
    int partition_index = -1;
};

struct IndexVerification {
    size_t segment_index = 0;
    bool pass = false;
    int entries_checked = 0;
    int entries_matched = 0;
    int track_index = -1;
    std::array<uint8_t, 4> track_number{};
    std::vector<std::string> failures;
};

struct Document {
    std::string path;
    uint64_t file_size = 0;
    size_t run_in = 0;
    std::vector<Partition> partitions;
    std::vector<Descriptor> descriptors;
    std::vector<Track> tracks;
    std::vector<TimecodeInfo> timecodes;
    std::vector<IndexSegment> index_segments;
    std::vector<EssenceElement> essence;
    std::vector<IndexVerification> index_verifications;
    std::vector<std::string> warnings;
    size_t primer_count = 0;
    size_t metadata_set_count = 0;
    size_t fill_klv_count = 0;
    size_t unknown_klv_count = 0;

    const Partition* partition_for_stream_offset(uint32_t body_sid,
                                                 uint64_t stream_offset) const;
    int find_track_by_number(const std::array<uint8_t, 4>& number) const;
};

Document parse_mxf_file(const std::string& path);
void verify_index_tables(Document& document);

TrackKind kind_from_data_definition(const Uid& ul);
std::string track_kind_name(TrackKind kind);
std::string partition_type_name(PartitionType type);

}  // namespace mxf
