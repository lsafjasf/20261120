#pragma once

#include <string>

#include "common.h"

namespace hevclab {

struct YuvDesc {
  int width = 0;
  int height = 0;
  int frames = 0;
  int bit_depth = 10;
};

void compute_boundary_metrics(const std::string &source_yuv,
                              const std::string &decoded_yuv,
                              const std::string &boundary_csv,
                              const YuvDesc &desc,
                              const std::string &frame_csv,
                              const std::string &seam_csv);

void compute_subband_metrics(const std::string &source_yuv,
                             const std::string &decoded_yuv,
                             const YuvDesc &desc,
                             const std::string &csv_path);

std::uint64_t file_size(const std::string &path);

}  // namespace hevclab
