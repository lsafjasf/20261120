#include "sample_gen.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <vector>

#include "common.h"

namespace hevclab {

namespace {

void write_plane(std::ofstream &out, const std::vector<std::uint16_t> &plane) {
  std::vector<char> bytes(plane.size() * 2);
  for (std::size_t i = 0; i < plane.size(); ++i) {
    bytes[i * 2] = static_cast<char>(plane[i] & 0xff);
    bytes[i * 2 + 1] = static_cast<char>((plane[i] >> 8) & 0xff);
  }
  out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

std::uint32_t hash_u32(std::uint32_t x, std::uint32_t y, std::uint32_t z) {
  std::uint32_t value = x * 0x9e3779b1u ^ y * 0x85ebca77u ^ z * 0xc2b2ae3du;
  value ^= value >> 16;
  value *= 0x7feb352du;
  value ^= value >> 15;
  value *= 0x846ca68bu;
  return value ^ (value >> 16);
}

int clamp_sample(int value, int bit_depth) {
  return std::clamp(value, 0, (1 << bit_depth) - 1);
}

}  // namespace

void generate_tile_stress_yuv420(const std::string &path,
                                 const SampleConfig &config) {
  if (config.bit_depth != 10) {
    throw Error("the bundled generator currently emits 10-bit samples");
  }
  if ((config.width & 1) || (config.height & 1)) {
    throw Error("4:2:0 sample dimensions must be even");
  }
  std::ofstream out(path, std::ios::binary);
  if (!out) throw Error("cannot create sample: " + path);

  const int width = config.width;
  const int height = config.height;
  for (int frame = 0; frame < config.frames; ++frame) {
    std::vector<std::uint16_t> y(width * height);
    std::vector<std::uint16_t> u((width / 2) * (height / 2));
    std::vector<std::uint16_t> v((width / 2) * (height / 2));

    for (int row = 0; row < height; ++row) {
      for (int col = 0; col < width; ++col) {
        const int gradient = 180 + (420 * col) / width + (150 * row) / height;
        const int checker =
            (((col >> 1) + (row >> 1) + frame) & 1) ? 95 : -95;
        const int diagonal = ((col + row + frame * 19) % 64 < 32) ? 35 : -35;
        const int grain = static_cast<int>(
            hash_u32(static_cast<std::uint32_t>(col),
                     static_cast<std::uint32_t>(row),
                     static_cast<std::uint32_t>(frame)) %
            81) -
            40;
        int value = gradient + checker + diagonal + grain;
        y[row * width + col] =
            static_cast<std::uint16_t>(clamp_sample(value, config.bit_depth));
      }
    }

    for (int row = 0; row < height / 2; ++row) {
      for (int col = 0; col < width / 2; ++col) {
        const double phase =
            2.0 * 3.14159265358979323846 *
            (static_cast<double>(frame) / 7.0);
        const int u_value =
            512 + static_cast<int>(90.0 * std::sin(col * 0.035 + phase)) +
            static_cast<int>(35.0 * std::cos(row * 0.052 - phase));
        const int v_value =
            512 + static_cast<int>(85.0 * std::cos(col * 0.028 - phase)) -
            static_cast<int>(40.0 * std::sin(row * 0.045 + phase));
        u[row * (width / 2) + col] = static_cast<std::uint16_t>(
            clamp_sample(u_value, config.bit_depth));
        v[row * (width / 2) + col] = static_cast<std::uint16_t>(
            clamp_sample(v_value, config.bit_depth));
      }
    }
    write_plane(out, y);
    write_plane(out, u);
    write_plane(out, v);
  }
}

void generate_high_subband_yuv420(const std::string &path,
                                  const SampleConfig &config) {
  if (config.bit_depth != 10) {
    throw Error("the bundled generator currently emits 10-bit samples");
  }
  if ((config.width & 1) || (config.height & 1)) {
    throw Error("4:2:0 sample dimensions must be even");
  }
  std::ofstream out(path, std::ios::binary);
  if (!out) throw Error("cannot create sample: " + path);

  const int width = config.width;
  const int height = config.height;
  const int mid = 1 << (config.bit_depth - 1);
  for (int frame = 0; frame < config.frames; ++frame) {
    std::vector<std::uint16_t> y(width * height);
    std::vector<std::uint16_t> chroma((width / 2) * (height / 2),
                                      static_cast<std::uint16_t>(mid));
    for (int row = 0; row < height; ++row) {
      for (int col = 0; col < width; ++col) {
        const bool positive = ((col + row + frame) & 1) != 0;
        y[row * width + col] = static_cast<std::uint16_t>(
            mid + (positive ? 160 : -160));
      }
    }
    write_plane(out, y);
    write_plane(out, chroma);
    write_plane(out, chroma);
  }
}

}  // namespace hevclab
