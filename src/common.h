#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace hevclab {

struct Rgb8 {
  std::uint8_t r;
  std::uint8_t g;
  std::uint8_t b;
};

struct TileRect {
  int id = 0;
  int col = 0;
  int row = 0;
  int x0 = 0;
  int y0 = 0;
  int x1 = 0;
  int y1 = 0;
  int ctu_x0 = 0;
  int ctu_y0 = 0;
  int ctu_x1 = 0;
  int ctu_y1 = 0;

  int width() const { return x1 - x0; }
  int height() const { return y1 - y0; }
  int ctu_count() const {
    return (ctu_x1 - ctu_x0) * (ctu_y1 - ctu_y0);
  }
};

struct CtuStat {
  int frame = 0;
  int poc = 0;
  int x = 0;
  int y = 0;
  std::uint32_t bits = 0;
  int intra_cu = 0;
  int inter_cu = 0;
  int skip_cu = 0;
  int pcm_cu = 0;
  int min_depth = 0;
  int max_depth = 0;
  int tile_id = -1;

  std::string type() const;
};

struct PpsInfo {
  int pps_id = -1;
  int sps_id = -1;
  bool tiles_enabled = false;
  bool entropy_coding_sync_enabled = false;
  bool loop_filter_across_tiles_enabled = false;
  int tile_cols = 1;
  int tile_rows = 1;
  std::vector<int> col_widths;
  std::vector<int> row_heights;
};

struct SpsInfo {
  int sps_id = -1;
  int width = 0;
  int height = 0;
  int coded_width = 0;
  int coded_height = 0;
  int chroma_format_idc = 0;
  bool separate_colour_plane = false;
  int bit_depth_luma = 0;
  int bit_depth_chroma = 0;
  int ctu_size = 64;
  int ctu_cols = 0;
  int ctu_rows = 0;
  int log2_max_poc_lsb = 0;
  bool sao_enabled = false;
  bool long_term_ref_pics_present = false;
  int num_long_term_ref_pics_sps = 0;
  int num_short_term_ref_pic_sets = 0;
  bool temporal_mvp_enabled = false;
};

struct EntryPointInfo {
  int index = 0;
  std::uint32_t offset_minus1 = 0;
  std::uint32_t bytes = 0;
};

struct SliceInfo {
  int nal_index = -1;
  int nal_type = -1;
  std::uint64_t file_offset = 0;
  std::uint64_t nal_bytes = 0;
  std::uint64_t rbsp_bytes = 0;
  bool first_slice_segment_in_pic = false;
  bool dependent_slice_segment = false;
  bool is_irap = false;
  int pps_id = -1;
  int slice_type = -1;
  std::uint32_t slice_segment_address = 0;
  std::uint32_t pic_order_cnt_lsb = 0;
  int slice_qp_delta = 0;
  std::uint64_t header_bits = 0;
  std::uint64_t data_offset_bits = 0;
  std::uint64_t substream_bytes = 0;
  std::vector<EntryPointInfo> entry_points;
};

struct StreamInfo {
  std::uint64_t file_bytes = 0;
  int nal_count = 0;
  SpsInfo sps;
  PpsInfo pps;
  bool has_sps = false;
  bool has_pps = false;
  int unknown_nal_types = 0;
  std::vector<SliceInfo> slices;
  std::vector<TileRect> tiles;
};

class Error : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

std::string format_double(double value, int digits = 4);
std::vector<std::string> split_csv_line(const std::string &line);
std::string join_path(const std::string &a, const std::string &b);

}  // namespace hevclab
