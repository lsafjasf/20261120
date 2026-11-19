#pragma once

#include <string>

namespace hevclab {

struct SampleConfig {
  int width = 1024;
  int height = 512;
  int frames = 4;
  int fps = 25;
  int bit_depth = 10;
};

void generate_tile_stress_yuv420(const std::string &path,
                                 const SampleConfig &config);
void generate_high_subband_yuv420(const std::string &path,
                                  const SampleConfig &config);

}  // namespace hevclab
