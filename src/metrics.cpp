#include "metrics.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <tuple>
#include <vector>

namespace hevclab {

namespace {

class YuvReader {
 public:
  YuvReader(const std::string &path, const YuvDesc &desc)
      : in_(path, std::ios::binary), desc_(desc) {
    if (!in_) throw Error("cannot open YUV: " + path);
    if (desc_.bit_depth != 10) {
      throw Error("the metric reader currently supports 10-bit YUV only");
    }
  }

  bool read_y(std::vector<std::uint16_t> &y) {
    const std::size_t y_samples =
        static_cast<std::size_t>(desc_.width) * desc_.height;
    const std::size_t c_samples = y_samples / 4;
    y.resize(y_samples);
    if (!read_plane(y.data(), y_samples)) return false;
    std::vector<std::uint16_t> dummy(c_samples);
    if (!read_plane(dummy.data(), c_samples)) return false;
    if (!read_plane(dummy.data(), c_samples)) return false;
    return true;
  }

 private:
  bool read_plane(std::uint16_t *values, std::size_t count) {
    std::vector<char> bytes(count * 2);
    in_.read(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    if (in_.gcount() != static_cast<std::streamsize>(bytes.size())) {
      return false;
    }
    for (std::size_t i = 0; i < count; ++i) {
      values[i] = static_cast<std::uint16_t>(
          static_cast<std::uint8_t>(bytes[i * 2]) |
          (static_cast<std::uint16_t>(
               static_cast<std::uint8_t>(bytes[i * 2 + 1]))
           << 8));
    }
    return true;
  }

  std::ifstream in_;
  YuvDesc desc_;
};

struct BoundaryKey {
  int axis = 0;
  int coordinate = 0;
  bool operator<(const BoundaryKey &other) const {
    return std::tie(axis, coordinate) <
           std::tie(other.axis, other.coordinate);
  }
};

std::vector<BoundaryKey> read_internal_boundaries(const std::string &path,
                                                  int image_width,
                                                  int image_height) {
  std::ifstream in(path);
  if (!in) throw Error("cannot open boundary CSV: " + path);
  std::string line;
  std::getline(in, line);
  std::vector<std::string> header = split_csv_line(line);
  std::map<std::string, int> col;
  for (int i = 0; i < static_cast<int>(header.size()); ++i) {
    col[header[i]] = i;
  }
  if (!col.count("x1") || !col.count("y1")) {
    throw Error("boundary CSV lacks x1/y1 columns");
  }
  std::set<BoundaryKey> keys;
  while (std::getline(in, line)) {
    if (line.empty()) continue;
    const auto fields = split_csv_line(line);
    const int x1 = std::stoi(fields[col["x1"]]);
    const int y1 = std::stoi(fields[col["y1"]]);
    if (x1 > 0 && x1 < image_width) keys.insert({0, x1});
    if (y1 > 0 && y1 < image_height) keys.insert({1, y1});
  }
  return std::vector<BoundaryKey>(keys.begin(), keys.end());
}

double psnr_from_mse(double mse, int bit_depth) {
  if (mse <= 0.0) return std::numeric_limits<double>::infinity();
  const double peak = static_cast<double>((1 << bit_depth) - 1);
  return 10.0 * std::log10(peak * peak / mse);
}

struct RegionError {
  double mse = 0.0;
  double mae = 0.0;
  std::uint64_t count = 0;
};

RegionError vertical_band_error(const std::vector<std::uint16_t> &src,
                                const std::vector<std::uint16_t> &dec,
                                int width, int height, int center,
                                int half_width) {
  RegionError error;
  const int x0 = std::max(0, center - half_width);
  const int x1 = std::min(width, center + half_width);
  for (int y = 4; y < height - 4; ++y) {
    for (int x = x0; x < x1; ++x) {
      const double delta = static_cast<double>(src[y * width + x]) -
                           static_cast<double>(dec[y * width + x]);
      error.mse += delta * delta;
      error.mae += std::abs(delta);
      ++error.count;
    }
  }
  if (error.count) {
    error.mse /= error.count;
    error.mae /= error.count;
  }
  return error;
}

RegionError horizontal_band_error(const std::vector<std::uint16_t> &src,
                                  const std::vector<std::uint16_t> &dec,
                                  int width, int height, int center,
                                  int half_width) {
  RegionError error;
  const int y0 = std::max(0, center - half_width);
  const int y1 = std::min(height, center + half_width);
  for (int y = y0; y < y1; ++y) {
    for (int x = 4; x < width - 4; ++x) {
      const double delta = static_cast<double>(src[y * width + x]) -
                           static_cast<double>(dec[y * width + x]);
      error.mse += delta * delta;
      error.mae += std::abs(delta);
      ++error.count;
    }
  }
  if (error.count) {
    error.mse /= error.count;
    error.mae /= error.count;
  }
  return error;
}

double vertical_gradient_residual(const std::vector<std::uint16_t> &src,
                                  const std::vector<std::uint16_t> &dec,
                                  int width, int height, int x) {
  double sum = 0.0;
  std::uint64_t count = 0;
  for (int y = 4; y < height - 4; ++y) {
    const int src_step = static_cast<int>(src[y * width + x]) -
                         static_cast<int>(src[y * width + x - 1]);
    const int dec_step = static_cast<int>(dec[y * width + x]) -
                         static_cast<int>(dec[y * width + x - 1]);
    sum += std::abs(dec_step - src_step);
    ++count;
  }
  return count ? sum / count : 0.0;
}

double horizontal_gradient_residual(const std::vector<std::uint16_t> &src,
                                    const std::vector<std::uint16_t> &dec,
                                    int width, int y) {
  double sum = 0.0;
  std::uint64_t count = 0;
  for (int x = 4; x < width - 4; ++x) {
    const int src_step = static_cast<int>(src[y * width + x]) -
                         static_cast<int>(src[(y - 1) * width + x]);
    const int dec_step = static_cast<int>(dec[y * width + x]) -
                         static_cast<int>(dec[(y - 1) * width + x]);
    sum += std::abs(dec_step - src_step);
    ++count;
  }
  return count ? sum / count : 0.0;
}

double plane_mse(const std::vector<std::uint16_t> &src,
                 const std::vector<std::uint16_t> &dec, int width,
                 int height) {
  double mse = 0.0;
  for (int y = 0; y < height; ++y) {
    for (int x = 0; x < width; ++x) {
      const double delta = static_cast<double>(src[y * width + x]) -
                           static_cast<double>(dec[y * width + x]);
      mse += delta * delta;
    }
  }
  return mse / (static_cast<double>(width) * height);
}

}  // namespace

std::uint64_t file_size(const std::string &path) {
  std::ifstream in(path, std::ios::binary | std::ios::ate);
  if (!in) throw Error("cannot stat " + path);
  return static_cast<std::uint64_t>(in.tellg());
}

void compute_boundary_metrics(const std::string &source_yuv,
                              const std::string &decoded_yuv,
                              const std::string &boundary_csv,
                              const YuvDesc &desc,
                              const std::string &frame_csv,
                              const std::string &seam_csv) {
  YuvReader src(source_yuv, desc);
  YuvReader dec(decoded_yuv, desc);
  const auto boundaries =
      read_internal_boundaries(boundary_csv, desc.width, desc.height);
  std::ofstream frames(frame_csv);
  std::ofstream seams(seam_csv);
  if (!frames || !seams) throw Error("cannot create metric CSV output");
  frames << "poc,y_psnr_db,y_mse\n";
  seams << "poc,axis,coordinate,boundary_psnr_db,control_psnr_db,"
           "boundary_mae,control_mae,gradient_residual,control_gradient,"
           "step_excess,seam_ratio,visible_seam\n";

  std::vector<std::uint16_t> src_y;
  std::vector<std::uint16_t> dec_y;
  for (int poc = 0; poc < desc.frames; ++poc) {
    if (!src.read_y(src_y) || !dec.read_y(dec_y)) {
      throw Error("YUV frame count does not match requested frame count");
    }
    const double mse = plane_mse(src_y, dec_y, desc.width, desc.height);
    frames << poc << ',' << format_double(psnr_from_mse(mse, desc.bit_depth), 4)
           << ',' << format_double(mse, 4) << '\n';
    for (const BoundaryKey &boundary : boundaries) {
      RegionError boundary_error;
      RegionError control_a;
      RegionError control_b;
      double gradient = 0.0;
      double control_gradient_a = 0.0;
      double control_gradient_b = 0.0;
      int control_a_pos = 0;
      int control_b_pos = 0;
      if (boundary.axis == 0) {
        boundary_error = vertical_band_error(
            src_y, dec_y, desc.width, desc.height, boundary.coordinate, 2);
        control_a_pos = boundary.coordinate - 24;
        control_b_pos = boundary.coordinate + 24;
        if (control_a_pos > 2) {
          control_a = vertical_band_error(src_y, dec_y, desc.width, desc.height,
                                          control_a_pos, 2);
          control_gradient_a = vertical_gradient_residual(
              src_y, dec_y, desc.width, desc.height, control_a_pos);
        }
        if (control_b_pos < desc.width - 2) {
          control_b = vertical_band_error(src_y, dec_y, desc.width, desc.height,
                                          control_b_pos, 2);
          control_gradient_b = vertical_gradient_residual(
              src_y, dec_y, desc.width, desc.height, control_b_pos);
        }
        gradient = vertical_gradient_residual(src_y, dec_y, desc.width,
                                              desc.height, boundary.coordinate);
      } else {
        boundary_error = horizontal_band_error(
            src_y, dec_y, desc.width, desc.height, boundary.coordinate, 2);
        control_a_pos = boundary.coordinate - 24;
        control_b_pos = boundary.coordinate + 24;
        if (control_a_pos > 2) {
          control_a = horizontal_band_error(src_y, dec_y, desc.width,
                                            desc.height, control_a_pos, 2);
          control_gradient_a = horizontal_gradient_residual(
              src_y, dec_y, desc.width, control_a_pos);
        }
        if (control_b_pos < desc.height - 2) {
          control_b = horizontal_band_error(src_y, dec_y, desc.width,
                                            desc.height, control_b_pos, 2);
          control_gradient_b = horizontal_gradient_residual(
              src_y, dec_y, desc.width, control_b_pos);
        }
        gradient = horizontal_gradient_residual(
            src_y, dec_y, desc.width, boundary.coordinate);
      }
      const double control_mse =
          0.5 * (control_a.mse + control_b.mse);
      const double control_mae =
          0.5 * (control_a.mae + control_b.mae);
      const double control_gradient =
          0.5 * (control_gradient_a + control_gradient_b);
      const double seam_ratio =
          (gradient + 1e-9) / (control_gradient + 1e-9);
      const double step_excess =
          std::max(0.0, gradient - control_gradient);
      const double boundary_psnr =
          psnr_from_mse(boundary_error.mse, desc.bit_depth);
      const double control_psnr = psnr_from_mse(control_mse, desc.bit_depth);
      const bool visible =
          (seam_ratio > 1.25 && step_excess > 8.0) ||
          boundary_psnr + 2.0 < control_psnr;
      const char *axis = boundary.axis == 0 ? "V" : "H";
      seams << poc << ',' << axis << ',' << boundary.coordinate << ','
            << format_double(boundary_psnr, 4) << ','
            << format_double(control_psnr, 4) << ','
            << format_double(boundary_error.mae, 4) << ','
            << format_double(control_mae, 4) << ','
            << format_double(gradient, 4) << ','
            << format_double(control_gradient, 4) << ','
            << format_double(step_excess, 4) << ','
            << format_double(seam_ratio, 4) << ',' << (visible ? 1 : 0)
            << '\n';
    }
  }
}

void compute_subband_metrics(const std::string &source_yuv,
                             const std::string &decoded_yuv,
                             const YuvDesc &desc,
                             const std::string &csv_path) {
  YuvReader src(source_yuv, desc);
  YuvReader dec(decoded_yuv, desc);
  std::ofstream out(csv_path);
  if (!out) throw Error("cannot create " + csv_path);
  out << "poc,lowband_rms,lowband_max_abs,lowband_p999,lowband_mean,"
         "lowband_source_rms,highband_rms\n";
  std::vector<std::uint16_t> src_y;
  std::vector<std::uint16_t> dec_y;
  for (int poc = 0; poc < desc.frames; ++poc) {
    if (!src.read_y(src_y) || !dec.read_y(dec_y)) {
      throw Error("YUV frame count does not match requested frame count");
    }
    std::vector<double> low_src;
    std::vector<double> low_dec;
    std::vector<double> high_error;
    low_src.reserve((desc.width / 2) * (desc.height / 2));
    low_dec.reserve((desc.width / 2) * (desc.height / 2));
    for (int y = 0; y + 1 < desc.height; y += 2) {
      for (int x = 0; x + 1 < desc.width; x += 2) {
        double s = 0.0;
        double d = 0.0;
        for (int dy = 0; dy < 2; ++dy) {
          for (int dx = 0; dx < 2; ++dx) {
            const int index = (y + dy) * desc.width + x + dx;
            s += src_y[index];
            d += dec_y[index];
            const double hf = static_cast<double>(src_y[index]) -
                              static_cast<double>(dec_y[index]);
            high_error.push_back(hf);
          }
        }
        low_src.push_back(s / 4.0);
        low_dec.push_back(d / 4.0);
      }
    }
    double source_mean = 0.0;
    double decoded_mean = 0.0;
    for (std::size_t i = 0; i < low_src.size(); ++i) {
      source_mean += low_src[i];
      decoded_mean += low_dec[i];
    }
    source_mean /= low_src.size();
    decoded_mean /= low_dec.size();
    double source_sq = 0.0;
    double decoded_sq = 0.0;
    double high_sq = 0.0;
    std::vector<double> abs_dev;
    abs_dev.reserve(low_dec.size());
    for (std::size_t i = 0; i < low_src.size(); ++i) {
      const double ds = low_src[i] - source_mean;
      const double dd = low_dec[i] - decoded_mean;
      source_sq += ds * ds;
      decoded_sq += dd * dd;
      abs_dev.push_back(std::abs(dd));
    }
    for (double value : high_error) high_sq += value * value;
    std::sort(abs_dev.begin(), abs_dev.end());
    const double source_rms = std::sqrt(source_sq / low_src.size());
    const double decoded_rms = std::sqrt(decoded_sq / low_dec.size());
    const double high_rms = std::sqrt(high_sq / high_error.size());
    const double p999 =
        abs_dev[std::min(abs_dev.size() - 1,
                         static_cast<std::size_t>(0.999 * abs_dev.size()))];
    out << poc << ',' << format_double(decoded_rms, 6) << ','
        << format_double(abs_dev.back(), 6) << ',' << format_double(p999, 6)
        << ',' << format_double(decoded_mean, 6) << ','
        << format_double(source_rms, 6) << ','
        << format_double(high_rms, 6) << '\n';
  }
}

}  // namespace hevclab
