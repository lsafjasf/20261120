#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "common.h"
#include "hevc_parser.h"
#include "metrics.h"
#include "sample_gen.h"

namespace {

using namespace hevclab;

std::map<std::string, std::string> parse_options(int argc, char **argv,
                                                 int start) {
  std::map<std::string, std::string> options;
  for (int i = start; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg.rfind("--", 0) != 0) {
      throw Error("expected --option, got: " + arg);
    }
    if (i + 1 >= argc) throw Error("missing value for " + arg);
    options[arg.substr(2)] = argv[++i];
  }
  return options;
}

std::string required(const std::map<std::string, std::string> &options,
                     const std::string &key) {
  const auto it = options.find(key);
  if (it == options.end()) throw Error("missing --" + key);
  return it->second;
}

int optional_int(const std::map<std::string, std::string> &options,
                 const std::string &key, int fallback) {
  const auto it = options.find(key);
  return it == options.end() ? fallback : std::stoi(it->second);
}

void print_usage() {
  std::cout
      << "hevc-lab " << HEVC_LAB_VERSION << "\n"
      << "usage:\n"
      << "  hevc-lab gen-tile --out sample.yuv [--width 1024] [--height 512]\n"
      << "                      [--frames 4] [--fps 25]\n"
      << "  hevc-lab gen-subband --out sample.yuv [geometry options]\n"
      << "  hevc-lab analyze --input stream.hevc --ctu-csv prefixctu.csv\n"
      << "                   --prefix results/name\n"
      << "  hevc-lab metrics-boundary --source src.yuv --decoded dec.yuv\n"
      << "                   --boundaries name.boundaries.csv --width 1024\n"
      << "                   --height 512 --frames 4 --bit-depth 10\n"
      << "                   --frame-csv frames.csv --seam-csv seams.csv\n"
      << "  hevc-lab metrics-subband --source src.yuv --decoded dec.yuv\n"
      << "                   --width 1024 --height 512 --frames 4\n"
      << "                   --bit-depth 10 --csv subband.csv\n";
}

SampleConfig sample_options(const std::map<std::string, std::string> &options) {
  SampleConfig config;
  config.width = optional_int(options, "width", 1024);
  config.height = optional_int(options, "height", 512);
  config.frames = optional_int(options, "frames", 4);
  config.fps = optional_int(options, "fps", 25);
  config.bit_depth = optional_int(options, "bit-depth", 10);
  return config;
}

void print_stream_summary(const StreamInfo &info) {
  std::cout << "resolution=" << info.sps.width << 'x' << info.sps.height
            << '\n';
  std::cout << "coded_resolution=" << info.sps.coded_width << 'x'
            << info.sps.coded_height << '\n';
  std::cout << "bit_depth=" << info.sps.bit_depth_luma << " (luma), "
            << info.sps.bit_depth_chroma << " (chroma)\n";
  std::cout << "chroma_format=" << chroma_format_name(info.sps) << '\n';
  std::cout << "ctu_size=" << info.sps.ctu_size << '\n';
  std::cout << "ctu_grid=" << info.sps.ctu_cols << 'x' << info.sps.ctu_rows
            << '\n';
  std::cout << "tiles=" << info.pps.tile_cols << 'x' << info.pps.tile_rows
            << " count=" << info.tiles.size() << '\n';
  std::cout << "loop_filter_across_tiles="
            << (info.pps.loop_filter_across_tiles_enabled ? "enabled"
                                                          : "disabled")
            << '\n';
  std::cout << "slice_segments=" << info.slices.size() << '\n';
  for (const SliceInfo &slice : info.slices) {
    std::cout << "  slice nal=" << slice.nal_index << " type="
              << nal_type_name(slice.nal_type) << " slice_type="
              << slice_type_name(slice.slice_type)
              << " first=" << (slice.first_slice_segment_in_pic ? 1 : 0)
              << " address=" << slice.slice_segment_address
              << " header_bits=" << slice.header_bits
              << " entry_points=" << slice.entry_points.size()
              << " data_bytes=" << slice.substream_bytes << '\n';
  }
  for (const TileRect &tile : info.tiles) {
    std::cout << "  tile " << tile.id << " col=" << tile.col
              << " row=" << tile.row << " pixel=[" << tile.x0 << ','
              << tile.y0 << ")-[" << tile.x1 << ',' << tile.y1
              << ") ctu=[" << tile.ctu_x0 << ',' << tile.ctu_y0 << ")-["
              << tile.ctu_x1 << ',' << tile.ctu_y1
              << ") ctu_count=" << tile.ctu_count() << '\n';
  }
}

int run(int argc, char **argv) {
  if (argc < 2) {
    print_usage();
    return 0;
  }
  const std::string command = argv[1];
  if (command == "--help" || command == "-h" || command == "help") {
    print_usage();
    return 0;
  }
  const auto options = parse_options(argc, argv, 2);

  if (command == "gen-tile") {
    const SampleConfig config = sample_options(options);
    generate_tile_stress_yuv420(required(options, "out"), config);
    return 0;
  }
  if (command == "gen-subband") {
    const SampleConfig config = sample_options(options);
    generate_high_subband_yuv420(required(options, "out"), config);
    return 0;
  }
  if (command == "analyze") {
    StreamInfo info = parse_hevc_file(required(options, "input"));
    std::vector<CtuStat> stats;
    if (options.count("ctu-csv")) {
      stats = read_ctu_csv(options.at("ctu-csv"));
      annotate_tiles(info, stats);
    }
    const std::string prefix = required(options, "prefix");
    const std::filesystem::path prefix_path(prefix);
    if (prefix_path.has_parent_path()) {
      std::filesystem::create_directories(prefix_path.parent_path());
    }
    write_boundary_csv(info, stats, prefix + ".boundaries.csv");
    write_slices_csv(info, prefix + ".slices.csv");
    if (!stats.empty()) {
      write_ctu_annotated_csv(stats, prefix + ".ctu.csv");
      write_tile_summary_csv(info, stats, prefix + ".tiles.csv");
      write_tile_ctu_csv(info, stats, prefix + ".tile-ctu.csv");
    }
    std::ofstream json(prefix + ".info.txt");
    if (!json) throw Error("cannot write info output");
    json << "resolution=" << info.sps.width << 'x' << info.sps.height << '\n'
         << "coded_resolution=" << info.sps.coded_width << 'x'
         << info.sps.coded_height << '\n'
         << "bit_depth_luma=" << info.sps.bit_depth_luma << '\n'
         << "bit_depth_chroma=" << info.sps.bit_depth_chroma << '\n'
         << "chroma_format=" << chroma_format_name(info.sps) << '\n'
         << "ctu_size=" << info.sps.ctu_size << '\n'
         << "ctu_grid=" << info.sps.ctu_cols << 'x' << info.sps.ctu_rows
         << '\n'
         << "tile_grid=" << info.pps.tile_cols << 'x' << info.pps.tile_rows
         << '\n'
         << "loop_filter_across_tiles="
         << (info.pps.loop_filter_across_tiles_enabled ? 1 : 0) << '\n';
    for (const SliceInfo &slice : info.slices) {
      json << "slice=" << slice.nal_index << ",type="
           << slice_type_name(slice.slice_type)
           << ",nal_type=" << nal_type_name(slice.nal_type)
           << ",header_bits=" << slice.header_bits
           << ",data_bytes=" << slice.substream_bytes
           << ",entry_points=" << slice.entry_points.size() << '\n';
    }
    print_stream_summary(info);
    return 0;
  }
  if (command == "metrics-boundary") {
    YuvDesc desc;
    desc.width = std::stoi(required(options, "width"));
    desc.height = std::stoi(required(options, "height"));
    desc.frames = std::stoi(required(options, "frames"));
    desc.bit_depth = std::stoi(required(options, "bit-depth"));
    compute_boundary_metrics(
        required(options, "source"), required(options, "decoded"),
        required(options, "boundaries"), desc, required(options, "frame-csv"),
        required(options, "seam-csv"));
    return 0;
  }
  if (command == "metrics-subband") {
    YuvDesc desc;
    desc.width = std::stoi(required(options, "width"));
    desc.height = std::stoi(required(options, "height"));
    desc.frames = std::stoi(required(options, "frames"));
    desc.bit_depth = std::stoi(required(options, "bit-depth"));
    compute_subband_metrics(required(options, "source"),
                            required(options, "decoded"), desc,
                            required(options, "csv"));
    return 0;
  }
  throw Error("unknown command: " + command);
}

}  // namespace

int main(int argc, char **argv) {
  try {
    return run(argc, argv);
  } catch (const std::exception &error) {
    std::cerr << "hevc-lab: " << error.what() << '\n';
    return 1;
  }
}
