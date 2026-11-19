#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "common.h"

namespace hevclab {

StreamInfo parse_hevc_annex_b(const std::vector<std::uint8_t> &bytes);
StreamInfo parse_hevc_file(const std::string &path);
std::vector<CtuStat> read_ctu_csv(const std::string &path);
void annotate_tiles(StreamInfo &info, std::vector<CtuStat> &ctu_stats);

std::string chroma_format_name(const SpsInfo &sps);
std::string slice_type_name(int type);
std::string nal_type_name(int type);

void write_boundary_csv(const StreamInfo &info,
                        const std::vector<CtuStat> &stats,
                        const std::string &path);
void write_slices_csv(const StreamInfo &info, const std::string &path);
void write_tile_ctu_csv(const StreamInfo &info,
                        const std::vector<CtuStat> &stats,
                        const std::string &path);
void write_tile_summary_csv(const StreamInfo &info,
                            const std::vector<CtuStat> &stats,
                            const std::string &path);
void write_ctu_annotated_csv(const std::vector<CtuStat> &stats,
                             const std::string &path);

}  // namespace hevclab
