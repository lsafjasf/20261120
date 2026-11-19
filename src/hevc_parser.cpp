#include "hevc_parser.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <map>
#include <numeric>
#include <set>
#include <sstream>

#include "bit_reader.h"

namespace hevclab {

namespace {

struct NalUnit {
  int index = 0;
  int type = -1;
  std::uint64_t file_offset = 0;
  std::uint64_t bytes = 0;
  std::vector<std::uint8_t> rbsp;
};

struct RpsState {
  int num_negative = 0;
  int num_positive = 0;
  int num_delta_pocs = 0;
};

struct ParseContext {
  SpsInfo sps;
  PpsInfo pps;
  bool has_sps = false;
  bool has_pps = false;
  std::vector<RpsState> sps_rps;
  bool dependent_slices = false;
  bool output_flag_present = false;
  int extra_slice_header_bits = 0;
  bool cabac_init_present = false;
  int default_l0_active_minus1 = 0;
  int default_l1_active_minus1 = 0;
  bool weighted_pred = false;
  bool weighted_bipred = false;
  bool chroma_qp_offsets_present = false;
  bool deblocking_filter_control_present = false;
  bool loop_filter_across_slices_enabled = false;
  bool lists_modification_present = false;
  bool slice_header_extension_present = false;
};

bool is_irap(int nal_type) { return nal_type >= 16 && nal_type <= 23; }

bool is_idr(int nal_type) { return nal_type == 19 || nal_type == 20; }

int ceil_log2(std::uint32_t value) {
  if (value <= 1) return 0;
  return 32 - __builtin_clz(value - 1);
}

void skip_profile_tier_level(BitReader &br, int max_sub_layers_minus1) {
  br.u(2);
  br.u(1);
  br.u(5);
  br.u(32);
  br.skip(48);
  br.u(8);
  bool profile_present[8] = {};
  bool level_present[8] = {};
  for (int i = 0; i < max_sub_layers_minus1; ++i) {
    profile_present[i] = br.flag();
    level_present[i] = br.flag();
  }
  if (max_sub_layers_minus1 > 0) {
    for (int i = max_sub_layers_minus1; i < 8; ++i) {
      br.u(2);
    }
  }
  for (int i = 0; i < max_sub_layers_minus1; ++i) {
    if (profile_present[i]) {
      br.u(2);
      br.u(1);
      br.u(5);
      br.u(32);
      br.skip(48);
    }
    if (level_present[i]) br.u(8);
  }
}

void skip_scaling_list_data(BitReader &br) {
  for (int size_id = 0; size_id < 4; ++size_id) {
    const int step = size_id == 3 ? 3 : 1;
    for (int matrix_id = 0; matrix_id < 6; matrix_id += step) {
      const bool pred_mode = br.flag();
      if (!pred_mode) {
        br.ue();
      } else {
        const int coef_num = std::min(64, 1 << (4 + (size_id << 1)));
        if (size_id > 1) br.se();
        for (int i = 0; i < coef_num; ++i) br.se();
      }
    }
  }
}

RpsState parse_short_term_ref_pic_set(BitReader &br,
                                      const std::vector<RpsState> &prior,
                                      int index) {
  RpsState state;
  bool inter_pred = false;
  if (index != 0) inter_pred = br.flag();
  if (inter_pred) {
    int delta_idx_minus1 = 0;
    if (index == static_cast<int>(prior.size())) {
      delta_idx_minus1 = static_cast<int>(br.ue());
    }
    const int ref_idx = index - (delta_idx_minus1 + 1);
    if (ref_idx < 0 || ref_idx >= static_cast<int>(prior.size())) {
      throw Error("invalid reference RPS index");
    }
    br.flag();
    br.ue();
    const int ref_count = prior[ref_idx].num_delta_pocs;
    std::vector<bool> used(ref_count + 1, false);
    for (int j = 0; j <= ref_count; ++j) {
      used[j] = br.flag();
      if (!used[j]) br.flag();
    }
    state.num_delta_pocs = static_cast<int>(
        std::count(used.begin(), used.end(), true));
    state.num_negative = prior[ref_idx].num_negative;
    state.num_positive = prior[ref_idx].num_positive;
    return state;
  }

  state.num_negative = static_cast<int>(br.ue());
  state.num_positive = static_cast<int>(br.ue());
  if (state.num_negative > 16 || state.num_positive > 16) {
    throw Error("invalid short-term RPS size");
  }
  for (int i = 0; i < state.num_negative; ++i) {
    br.ue();
    br.flag();
  }
  for (int i = 0; i < state.num_positive; ++i) {
    br.ue();
    br.flag();
  }
  state.num_delta_pocs = state.num_negative + state.num_positive;
  return state;
}

void parse_sps(BitReader &br, ParseContext &ctx) {
  br.u(4);
  const int max_sub_layers_minus1 = static_cast<int>(br.u(3));
  br.u(1);
  skip_profile_tier_level(br, max_sub_layers_minus1);

  SpsInfo sps;
  sps.sps_id = static_cast<int>(br.ue());
  sps.chroma_format_idc = static_cast<int>(br.ue());
  if (sps.chroma_format_idc == 3) sps.separate_colour_plane = br.flag();
  sps.width = static_cast<int>(br.ue());
  sps.height = static_cast<int>(br.ue());

  int sub_width_c = 1;
  int sub_height_c = 1;
  if (sps.chroma_format_idc == 1) {
    sub_width_c = 2;
    sub_height_c = 2;
  } else if (sps.chroma_format_idc == 2) {
    sub_width_c = 2;
  }

  if (br.flag()) {
    br.ue();
    br.ue();
    br.ue();
    br.ue();
  }
  sps.bit_depth_luma = static_cast<int>(br.ue()) + 8;
  sps.bit_depth_chroma = static_cast<int>(br.ue()) + 8;
  sps.log2_max_poc_lsb = static_cast<int>(br.ue()) + 4;

  const bool ordering_present = br.flag();
  const int ordering_start = ordering_present ? 0 : max_sub_layers_minus1;
  for (int i = ordering_start; i <= max_sub_layers_minus1; ++i) {
    br.ue();
    br.ue();
    br.ue();
  }

  const int log2_min_cb = static_cast<int>(br.ue()) + 3;
  const int log2_diff_cb = static_cast<int>(br.ue());
  sps.ctu_size = 1 << (log2_min_cb + log2_diff_cb);
  sps.ctu_cols = (sps.width + sps.ctu_size - 1) / sps.ctu_size;
  sps.ctu_rows = (sps.height + sps.ctu_size - 1) / sps.ctu_size;
  sps.coded_width = sps.ctu_cols * sps.ctu_size;
  sps.coded_height = sps.ctu_rows * sps.ctu_size;
  (void)sub_width_c;
  (void)sub_height_c;

  br.ue();
  br.ue();
  br.ue();
  br.ue();
  if (br.flag()) {
    if (br.flag()) skip_scaling_list_data(br);
  }
  br.flag();
  sps.sao_enabled = br.flag();
  if (br.flag()) {
    br.u(4);
    br.u(4);
    br.ue();
    br.ue();
    br.flag();
  }

  sps.num_short_term_ref_pic_sets = static_cast<int>(br.ue());
  std::vector<RpsState> rps;
  rps.reserve(sps.num_short_term_ref_pic_sets);
  for (int i = 0; i < sps.num_short_term_ref_pic_sets; ++i) {
    rps.push_back(parse_short_term_ref_pic_set(br, rps, i));
  }
  sps.long_term_ref_pics_present = br.flag();
  if (sps.long_term_ref_pics_present) {
    sps.num_long_term_ref_pics_sps = static_cast<int>(br.ue());
    for (int i = 0; i < sps.num_long_term_ref_pics_sps; ++i) {
      br.u(sps.log2_max_poc_lsb);
      br.flag();
    }
  }
  sps.temporal_mvp_enabled = br.flag();
  br.flag();
  br.flag();

  ctx.sps = sps;
  ctx.sps_rps = std::move(rps);
  ctx.has_sps = true;
}

void parse_pps(BitReader &br, ParseContext &ctx) {
  PpsInfo pps;
  pps.pps_id = static_cast<int>(br.ue());
  pps.sps_id = static_cast<int>(br.ue());
  ctx.dependent_slices = br.flag();
  ctx.output_flag_present = br.flag();
  ctx.extra_slice_header_bits = static_cast<int>(br.u(3));

  br.flag();
  ctx.cabac_init_present = br.flag();
  ctx.default_l0_active_minus1 = static_cast<int>(br.ue());
  ctx.default_l1_active_minus1 = static_cast<int>(br.ue());
  br.se();
  br.flag();
  br.flag();
  const bool cu_qp_delta_enabled = br.flag();
  if (cu_qp_delta_enabled) br.ue();
  br.se();
  br.se();
  ctx.chroma_qp_offsets_present = br.flag();
  ctx.weighted_pred = br.flag();
  ctx.weighted_bipred = br.flag();
  br.flag();

  pps.tiles_enabled = br.flag();
  pps.entropy_coding_sync_enabled = br.flag();
  if (pps.tiles_enabled) {
    const int cols_minus1 = static_cast<int>(br.ue());
    const int rows_minus1 = static_cast<int>(br.ue());
    pps.tile_cols = cols_minus1 + 1;
    pps.tile_rows = rows_minus1 + 1;
    const bool uniform_spacing = br.flag();
    if (uniform_spacing) {
      if (!ctx.has_sps) throw Error("PPS parsed before SPS");
      const int cols = ctx.sps.ctu_cols;
      const int rows = ctx.sps.ctu_rows;
      for (int i = 0; i < pps.tile_cols; ++i) {
        pps.col_widths.push_back(
            (i + 1) * cols / pps.tile_cols - i * cols / pps.tile_cols);
      }
      for (int i = 0; i < pps.tile_rows; ++i) {
        pps.row_heights.push_back(
            (i + 1) * rows / pps.tile_rows - i * rows / pps.tile_rows);
      }
    } else {
      int used_cols = 0;
      int used_rows = 0;
      for (int i = 0; i < pps.tile_cols - 1; ++i) {
        const int width = static_cast<int>(br.ue()) + 1;
        pps.col_widths.push_back(width);
        used_cols += width;
      }
      for (int i = 0; i < pps.tile_rows - 1; ++i) {
        const int height = static_cast<int>(br.ue()) + 1;
        pps.row_heights.push_back(height);
        used_rows += height;
      }
      if (!ctx.has_sps) throw Error("PPS parsed before SPS");
      pps.col_widths.push_back(ctx.sps.ctu_cols - used_cols);
      pps.row_heights.push_back(ctx.sps.ctu_rows - used_rows);
    }
    pps.loop_filter_across_tiles_enabled = br.flag();
  }
  if (!pps.tiles_enabled) {
    if (!ctx.has_sps) throw Error("PPS parsed before SPS");
    pps.tile_cols = 1;
    pps.tile_rows = 1;
    pps.col_widths = {ctx.sps.ctu_cols};
    pps.row_heights = {ctx.sps.ctu_rows};
  }

  ctx.loop_filter_across_slices_enabled = br.flag();
  ctx.deblocking_filter_control_present = br.flag();
  if (ctx.deblocking_filter_control_present) {
    br.flag();
    if (!br.flag()) {
      br.se();
      br.se();
    }
  }
  if (br.flag()) skip_scaling_list_data(br);
  ctx.lists_modification_present = br.flag();
  br.ue();
  ctx.slice_header_extension_present = br.flag();
  br.flag();

  ctx.pps = pps;
  ctx.has_pps = true;
}

std::vector<NalUnit> split_annex_b(const std::vector<std::uint8_t> &bytes) {
  struct Start {
    std::size_t pos;
    std::size_t prefix;
  };
  std::vector<Start> starts;
  for (std::size_t i = 0; i + 3 <= bytes.size(); ++i) {
    if (bytes[i] == 0 && bytes[i + 1] == 0 && bytes[i + 2] == 1) {
      const std::size_t prefix = (i > 0 && bytes[i - 1] == 0) ? 4 : 3;
      starts.push_back({i - (prefix == 4 ? 1 : 0), prefix});
      i += 2;
    }
  }
  if (starts.empty()) throw Error("no Annex-B start code found");

  std::vector<NalUnit> nals;
  for (std::size_t i = 0; i < starts.size(); ++i) {
    const std::size_t payload_start = starts[i].pos + starts[i].prefix;
    const std::size_t payload_end =
        i + 1 < starts.size() ? starts[i + 1].pos : bytes.size();
    if (payload_end <= payload_start + 1) continue;
    NalUnit nal;
    nal.index = static_cast<int>(nals.size());
    nal.file_offset = starts[i].pos;
    nal.bytes = payload_end - starts[i].pos;
    nal.type = (bytes[payload_start] >> 1) & 0x3f;
    nal.rbsp = remove_emulation_prevention(bytes.data() + payload_start + 2,
                                           payload_end - payload_start - 2);
    nals.push_back(std::move(nal));
  }
  return nals;
}

void parse_weighted_prediction_table(BitReader &br, int chroma_format_idc,
                                     int num_l0, int num_l1) {
  br.ue();
  if (chroma_format_idc == 1) {
    br.se();
  }
  for (int list = 0; list < 2; ++list) {
    const int count = list == 0 ? num_l0 : num_l1;
    for (int i = 0; i < count; ++i) {
      if (br.flag()) {
        br.se();
        br.se();
      }
      if (chroma_format_idc != 0 && br.flag()) {
        for (int component = 0; component < 2; ++component) {
          br.se();
          br.se();
        }
      }
    }
  }
}

void skip_ref_pic_lists_modification(BitReader &br, int slice_type,
                                     int num_l0, int num_l1) {
  if (num_l0 > 0) {
    const int bits = ceil_log2(num_l0);
    for (int i = 0; i < num_l0; ++i) br.u(bits);
  }
  if (slice_type == 0 && num_l1 > 0) {
    const int bits = ceil_log2(num_l1);
    for (int i = 0; i < num_l1; ++i) br.u(bits);
  }
}

SliceInfo parse_slice_header(BitReader &br, const NalUnit &nal,
                             ParseContext &ctx) {
  if (!ctx.has_sps || !ctx.has_pps) {
    throw Error("slice encountered before SPS/PPS");
  }
  SliceInfo slice;
  slice.nal_index = nal.index;
  slice.nal_type = nal.type;
  slice.file_offset = nal.file_offset;
  slice.nal_bytes = nal.bytes;
  slice.rbsp_bytes = nal.rbsp.size();
  slice.first_slice_segment_in_pic = br.flag();
  slice.is_irap = is_irap(nal.type);
  if (slice.is_irap) br.flag();
  slice.pps_id = static_cast<int>(br.ue());
  if (slice.pps_id != ctx.pps.pps_id) {
    throw Error("slice references an unsupported PPS");
  }

  if (!slice.first_slice_segment_in_pic) {
    if (ctx.dependent_slices) slice.dependent_slice_segment = br.flag();
    const int address_bits = ceil_log2(ctx.sps.ctu_cols * ctx.sps.ctu_rows);
    slice.slice_segment_address = br.u(address_bits);
  }

  int slice_temporal_mvp = 0;
  int sao_luma = 0;
  int sao_chroma = 0;
  bool deblocking_disabled = false;
  int num_l0 = ctx.default_l0_active_minus1 + 1;
  int num_l1 = ctx.default_l1_active_minus1 + 1;

  if (!slice.dependent_slice_segment) {
    if (ctx.extra_slice_header_bits > 0) br.skip(ctx.extra_slice_header_bits);
    slice.slice_type = static_cast<int>(br.ue());
    if (slice.slice_type < 0 || slice.slice_type > 2) {
      throw Error("invalid slice_type");
    }
    if (ctx.output_flag_present) br.flag();
    if (ctx.sps.separate_colour_plane) br.u(2);

    if (!is_idr(nal.type)) {
      slice.pic_order_cnt_lsb = br.u(ctx.sps.log2_max_poc_lsb);
      const bool rps_from_sps = br.flag();
      if (!rps_from_sps) {
        auto rps = ctx.sps_rps;
        parse_short_term_ref_pic_set(br, rps, static_cast<int>(rps.size()));
      } else if (ctx.sps.num_short_term_ref_pic_sets > 1) {
        br.u(ceil_log2(ctx.sps.num_short_term_ref_pic_sets));
      }
      if (ctx.sps.temporal_mvp_enabled) slice_temporal_mvp = br.flag();
      if (ctx.sps.long_term_ref_pics_present) {
        const int num_sps = static_cast<int>(br.ue());
        const int num_pics = static_cast<int>(br.ue());
        for (int i = 0; i < num_sps + num_pics; ++i) {
          if (i < num_sps && ctx.sps.num_long_term_ref_pics_sps > 0) {
            br.u(ceil_log2(ctx.sps.num_long_term_ref_pics_sps));
          }
          br.u(ctx.sps.log2_max_poc_lsb);
          br.flag();
          if (slice_temporal_mvp) {
            if (br.flag()) br.ue();
          }
        }
      }
      if ((ctx.weighted_pred && slice.slice_type == 1) ||
          (ctx.weighted_bipred && slice.slice_type == 0)) {
        parse_weighted_prediction_table(br, ctx.sps.chroma_format_idc,
                                        num_l0, num_l1);
      }
    }

    if (ctx.sps.sao_enabled) {
      sao_luma = br.flag();
      if (ctx.sps.chroma_format_idc != 0) sao_chroma = br.flag();
    }

    if (!is_idr(nal.type)) {
      if (br.flag()) {
        num_l0 = static_cast<int>(br.ue()) + 1;
        if (slice.slice_type == 0) num_l1 = static_cast<int>(br.ue()) + 1;
      }
      if (ctx.lists_modification_present && slice.slice_type != 2) {
        skip_ref_pic_lists_modification(br, slice.slice_type, num_l0, num_l1);
      }
    }
    if (ctx.cabac_init_present && slice.slice_type != 2) br.flag();

    if (slice_temporal_mvp && slice.slice_type != 2) {
      const bool collocated_from_l0 = br.flag();
      if ((collocated_from_l0 && num_l0 > 1) ||
          (!collocated_from_l0 && num_l1 > 1)) {
        br.ue();
      }
    }
    if (slice.slice_type != 2) br.ue();
    if (ctx.chroma_qp_offsets_present) {
      br.se();
      br.se();
    }
    slice.slice_qp_delta = br.se();
    if (ctx.sps.chroma_format_idc != 0 && ctx.chroma_qp_offsets_present) {
      br.flag();
    }

    if (ctx.deblocking_filter_control_present) {
      deblocking_disabled = br.flag();
      if (!deblocking_disabled && br.flag()) {
        br.se();
        br.se();
      }
    }
    if (ctx.loop_filter_across_slices_enabled &&
        (sao_luma || sao_chroma || !deblocking_disabled)) {
      br.flag();
    }
  }

  if (ctx.pps.tiles_enabled || ctx.pps.entropy_coding_sync_enabled) {
    const int num_offsets = static_cast<int>(br.ue());
    if (num_offsets < 0 || num_offsets > 4096) {
      throw Error("invalid num_entry_point_offsets");
    }
    if (num_offsets > 0) {
      const int offset_len = static_cast<int>(br.ue()) + 1;
      slice.entry_points.reserve(num_offsets);
      for (int i = 0; i < num_offsets; ++i) {
        EntryPointInfo entry;
        entry.index = i;
        entry.offset_minus1 = br.u(offset_len);
        entry.bytes = entry.offset_minus1 + 1;
        slice.entry_points.push_back(entry);
      }
    }
  }
  if (ctx.slice_header_extension_present) {
    const int extension_bytes = static_cast<int>(br.ue());
    br.skip(static_cast<std::size_t>(extension_bytes) * 8);
  }
  slice.header_bits = br.position();
  while (br.position() & 7) {
    br.skip(1);
  }
  slice.data_offset_bits = br.position();
  if (slice.data_offset_bits <= slice.rbsp_bytes * 8) {
    slice.substream_bytes =
        slice.rbsp_bytes - (slice.data_offset_bits / 8);
  }
  return slice;
}

}  // namespace

std::string CtuStat::type() const {
  if (pcm_cu > 0 && pcm_cu >= intra_cu && pcm_cu >= inter_cu &&
      pcm_cu >= skip_cu) {
    return "PCM";
  }
  if (skip_cu > 0 && skip_cu >= intra_cu && skip_cu >= inter_cu) {
    return "SKIP";
  }
  if (intra_cu >= inter_cu && intra_cu > 0) return "INTRA";
  if (inter_cu > 0) return "INTER";
  return "EMPTY";
}

std::string chroma_format_name(const SpsInfo &sps) {
  if (sps.separate_colour_plane) return "4:4:4 (separate planes)";
  switch (sps.chroma_format_idc) {
    case 0:
      return "4:0:0";
    case 1:
      return "4:2:0";
    case 2:
      return "4:2:2";
    case 3:
      return "4:4:4";
    default:
      return "unknown";
  }
}

std::string slice_type_name(int type) {
  switch (type) {
    case 0:
      return "B";
    case 1:
      return "P";
    case 2:
      return "I";
    default:
      return "unknown";
  }
}

std::string nal_type_name(int type) {
  switch (type) {
    case 32:
      return "VPS";
    case 33:
      return "SPS";
    case 34:
      return "PPS";
    case 35:
      return "AUD";
    case 39:
      return "PREFIX_SEI";
    case 40:
      return "SUFFIX_SEI";
    case 19:
      return "IDR_W_RADL";
    case 20:
      return "IDR_N_LP";
    case 21:
      return "CRA";
    default:
      return "VCL";
  }
}

StreamInfo parse_hevc_annex_b(const std::vector<std::uint8_t> &bytes) {
  const auto nals = split_annex_b(bytes);
  ParseContext ctx;
  StreamInfo info;
  info.file_bytes = bytes.size();
  info.nal_count = static_cast<int>(nals.size());

  for (const NalUnit &nal : nals) {
    BitReader br(nal.rbsp);
    try {
      if (nal.type == 33) {
        parse_sps(br, ctx);
      } else if (nal.type == 34) {
        parse_pps(br, ctx);
      } else if (nal.type <= 31) {
        info.slices.push_back(parse_slice_header(br, nal, ctx));
      } else if (nal.type != 32 && nal.type != 35 && nal.type != 39 &&
                 nal.type != 40) {
        info.unknown_nal_types++;
      }
    } catch (const std::exception &error) {
      throw Error("NAL " + std::to_string(nal.index) + " type " +
                  std::to_string(nal.type) + ": " + error.what());
    }
  }
  if (!ctx.has_sps || !ctx.has_pps) {
    throw Error("stream does not contain a complete SPS/PPS pair");
  }
  info.sps = ctx.sps;
  info.pps = ctx.pps;
  info.has_sps = true;
  info.has_pps = true;

  std::vector<int> col_ctus(ctx.pps.tile_cols + 1, 0);
  std::vector<int> row_ctus(ctx.pps.tile_rows + 1, 0);
  for (int col = 0; col < ctx.pps.tile_cols; ++col) {
    col_ctus[col + 1] = col_ctus[col] + ctx.pps.col_widths[col];
  }
  for (int row = 0; row < ctx.pps.tile_rows; ++row) {
    row_ctus[row + 1] = row_ctus[row] + ctx.pps.row_heights[row];
  }
  for (int row = 0; row < ctx.pps.tile_rows; ++row) {
    for (int col = 0; col < ctx.pps.tile_cols; ++col) {
      TileRect tile;
      tile.id = static_cast<int>(info.tiles.size());
      tile.col = col;
      tile.row = row;
      tile.ctu_x0 = col_ctus[col];
      tile.ctu_y0 = row_ctus[row];
      tile.ctu_x1 = col_ctus[col + 1];
      tile.ctu_y1 = row_ctus[row + 1];
      tile.x0 = std::min(tile.ctu_x0 * ctx.sps.ctu_size, ctx.sps.width);
      tile.y0 = std::min(tile.ctu_y0 * ctx.sps.ctu_size, ctx.sps.height);
      tile.x1 = std::min(tile.ctu_x1 * ctx.sps.ctu_size, ctx.sps.width);
      tile.y1 = std::min(tile.ctu_y1 * ctx.sps.ctu_size, ctx.sps.height);
      info.tiles.push_back(tile);
    }
  }
  return info;
}

StreamInfo parse_hevc_file(const std::string &path) {
  std::ifstream in(path, std::ios::binary);
  if (!in) throw Error("cannot open bitstream: " + path);
  std::vector<std::uint8_t> bytes(
      (std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  return parse_hevc_annex_b(bytes);
}

std::vector<CtuStat> read_ctu_csv(const std::string &path) {
  std::ifstream in(path);
  if (!in) throw Error("cannot open CTU CSV: " + path);
  std::string line;
  if (!std::getline(in, line)) throw Error("empty CTU CSV");
  std::vector<CtuStat> stats;
  while (std::getline(in, line)) {
    if (line.empty()) continue;
    const auto fields = split_csv_line(line);
    if (fields.size() < 11) throw Error("malformed CTU CSV row");
    CtuStat stat;
    stat.frame = std::stoi(fields[0]);
    stat.poc = std::stoi(fields[1]);
    stat.x = std::stoi(fields[2]);
    stat.y = std::stoi(fields[3]);
    stat.bits = static_cast<std::uint32_t>(std::stoul(fields[4]));
    stat.intra_cu = std::stoi(fields[5]);
    stat.inter_cu = std::stoi(fields[6]);
    stat.skip_cu = std::stoi(fields[7]);
    stat.pcm_cu = std::stoi(fields[8]);
    stat.min_depth = std::stoi(fields[9]);
    stat.max_depth = std::stoi(fields[10]);
    stats.push_back(stat);
  }
  return stats;
}

void annotate_tiles(StreamInfo &info, std::vector<CtuStat> &stats) {
  for (CtuStat &stat : stats) {
    stat.tile_id = -1;
    for (const TileRect &tile : info.tiles) {
      if (stat.x >= tile.ctu_x0 && stat.x < tile.ctu_x1 &&
          stat.y >= tile.ctu_y0 && stat.y < tile.ctu_y1) {
        stat.tile_id = tile.id;
        break;
      }
    }
    if (stat.tile_id < 0) throw Error("CTU not covered by any tile");
  }
}

std::string format_double(double value, int digits) {
  std::ostringstream out;
  out << std::fixed << std::setprecision(digits) << value;
  return out.str();
}

std::vector<std::string> split_csv_line(const std::string &line) {
  std::vector<std::string> fields;
  std::size_t start = 0;
  while (start <= line.size()) {
    const std::size_t comma = line.find(',', start);
    fields.push_back(line.substr(
        start, comma == std::string::npos ? std::string::npos : comma - start));
    if (comma == std::string::npos) break;
    start = comma + 1;
  }
  return fields;
}

std::string join_path(const std::string &a, const std::string &b) {
  return (std::filesystem::path(a) / b).string();
}

namespace {

struct BitSummary {
  int count = 0;
  std::uint64_t bits = 0;
  double mean = 0.0;
  double variance = 0.0;
  double stddev = 0.0;
  std::uint32_t min_bits = 0;
  std::uint32_t max_bits = 0;
};

BitSummary summarize_bits(const std::vector<std::uint32_t> &values) {
  BitSummary summary;
  if (values.empty()) return summary;
  summary.count = static_cast<int>(values.size());
  summary.min_bits = *std::min_element(values.begin(), values.end());
  summary.max_bits = *std::max_element(values.begin(), values.end());
  for (std::uint32_t value : values) summary.bits += value;
  summary.mean = static_cast<double>(summary.bits) / values.size();
  double squares = 0.0;
  for (std::uint32_t value : values) {
    const double delta = value - summary.mean;
    squares += delta * delta;
  }
  summary.variance = squares / values.size();
  summary.stddev = std::sqrt(summary.variance);
  return summary;
}

std::vector<int> unique_frames(const std::vector<CtuStat> &stats) {
  std::vector<int> frames;
  for (const CtuStat &stat : stats) {
    if (std::find(frames.begin(), frames.end(), stat.frame) == frames.end()) {
      frames.push_back(stat.frame);
    }
  }
  std::sort(frames.begin(), frames.end());
  return frames;
}

std::vector<std::uint32_t> bits_for_tile(const std::vector<CtuStat> &stats,
                                         int frame, int tile_id,
                                         bool all_frames) {
  std::vector<std::uint32_t> bits;
  for (const CtuStat &stat : stats) {
    if (stat.tile_id == tile_id && (all_frames || stat.frame == frame)) {
      bits.push_back(stat.bits);
    }
  }
  return bits;
}

std::vector<std::uint64_t> tile_entry_bytes(const StreamInfo &info,
                                            const SliceInfo *slice) {
  std::vector<std::uint64_t> bytes(info.tiles.size(), 0);
  if (!slice) return bytes;
  std::uint64_t offset_sum = 0;
  for (const EntryPointInfo &entry : slice->entry_points) {
    if (entry.index < static_cast<int>(bytes.size())) {
      bytes[entry.index] = entry.bytes;
    }
    offset_sum += entry.bytes;
  }
  if (slice->substream_bytes >= offset_sum) {
    bytes.back() = slice->substream_bytes - offset_sum;
  }
  return bytes;
}

}  // namespace

void write_ctu_annotated_csv(const std::vector<CtuStat> &stats,
                             const std::string &path) {
  std::ofstream out(path);
  if (!out) throw Error("cannot write " + path);
  out << "frame,poc,ctu_x,ctu_y,tile_id,bits,type,intra_cu,inter_cu,skip_cu,"
         "pcm_cu,min_depth,max_depth\n";
  for (const CtuStat &stat : stats) {
    out << stat.frame << ',' << stat.poc << ',' << stat.x << ',' << stat.y
        << ',' << stat.tile_id << ',' << stat.bits << ',' << stat.type() << ','
        << stat.intra_cu << ',' << stat.inter_cu << ',' << stat.skip_cu << ','
        << stat.pcm_cu << ',' << stat.min_depth << ',' << stat.max_depth
        << '\n';
  }
}

void write_tile_summary_csv(const StreamInfo &info,
                            const std::vector<CtuStat> &stats,
                            const std::string &path) {
  std::ofstream out(path);
  if (!out) throw Error("cannot write " + path);
  out << "frame,tile_id,tile_col,tile_row,x0,y0,x1,y1,ctu_count,ctu_bits,"
         "ctu_bytes,mean_bits,var_bits2,stddev_bits,min_bits,max_bits,"
         "intra_cu,inter_cu,skip_cu,pcm_cu\n";
  const auto frames = unique_frames(stats);
  const auto write_row = [&](int frame, const TileRect &tile, bool all_frames) {
    const auto bits = bits_for_tile(stats, frame, tile.id, all_frames);
    const BitSummary summary = summarize_bits(bits);
    int intra = 0;
    int inter = 0;
    int skip = 0;
    int pcm = 0;
    int ctu_count = 0;
    for (const CtuStat &stat : stats) {
      if (stat.tile_id != tile.id ||
          (!all_frames && stat.frame != frame)) {
        continue;
      }
      ++ctu_count;
      intra += stat.intra_cu;
      inter += stat.inter_cu;
      skip += stat.skip_cu;
      pcm += stat.pcm_cu;
    }
    out << frame << ',' << tile.id << ',' << tile.col << ',' << tile.row << ','
        << tile.x0 << ',' << tile.y0 << ',' << tile.x1 << ',' << tile.y1 << ','
        << ctu_count << ',' << summary.bits << ','
        << (summary.bits + 7) / 8 << ',' << format_double(summary.mean) << ','
        << format_double(summary.variance) << ','
        << format_double(summary.stddev) << ',' << summary.min_bits << ','
        << summary.max_bits << ',' << intra << ',' << inter << ',' << skip
        << ',' << pcm << '\n';
  };
  for (int frame : frames) {
    for (const TileRect &tile : info.tiles) write_row(frame, tile, false);
  }
  for (const TileRect &tile : info.tiles) write_row(-1, tile, true);
}

void write_tile_ctu_csv(const StreamInfo &info,
                        const std::vector<CtuStat> &stats,
                        const std::string &path) {
  std::ofstream out(path);
  if (!out) throw Error("cannot write " + path);
  out << "frame,tile_id,ctu_count,ctu_bits,first_ctu_x,first_ctu_y\n";
  for (int frame : unique_frames(stats)) {
    for (const TileRect &tile : info.tiles) {
      const auto bits = bits_for_tile(stats, frame, tile.id, false);
      std::uint64_t sum = 0;
      for (std::uint32_t value : bits) sum += value;
      out << frame << ',' << tile.id << ',' << bits.size() << ',' << sum << ','
          << tile.ctu_x0 << ',' << tile.ctu_y0 << '\n';
    }
  }
}

void write_boundary_csv(const StreamInfo &info,
                        const std::vector<CtuStat> &stats,
                        const std::string &path) {
  std::ofstream out(path);
  if (!out) throw Error("cannot write " + path);
  out << "frame,slice_nal,tile_id,tile_col,tile_row,x0,y0,x1,y1,width,height,"
         "ctu_x0,ctu_y0,ctu_x1,ctu_y1,ctu_count,ctu_bits,entry_offset_bytes,"
         "entry_end_x,entry_end_y,loop_filter_across_tiles\n";
  const auto frames = unique_frames(stats);
  const std::size_t frame_count =
      stats.empty() ? info.slices.size() : frames.size();
  for (std::size_t index = 0; index < frame_count; ++index) {
    const int frame = stats.empty() ? static_cast<int>(index) : frames[index];
    const SliceInfo *slice =
        index < info.slices.size() ? &info.slices[index] : nullptr;
    const auto entry_bytes = tile_entry_bytes(info, slice);
    for (const TileRect &tile : info.tiles) {
      const auto bits = bits_for_tile(stats, frame, tile.id, false);
      std::uint64_t bit_sum = 0;
      for (std::uint32_t value : bits) bit_sum += value;
      out << frame << ',' << (slice ? slice->nal_index : -1) << ',' << tile.id
          << ',' << tile.col << ',' << tile.row << ',' << tile.x0 << ','
          << tile.y0 << ',' << tile.x1 << ',' << tile.y1 << ',' << tile.width()
          << ',' << tile.height() << ',' << tile.ctu_x0 << ',' << tile.ctu_y0
          << ',' << tile.ctu_x1 << ',' << tile.ctu_y1 << ','
          << tile.ctu_count() << ',' << bit_sum << ','
          << entry_bytes[tile.id] << ',' << tile.x1 << ',' << tile.y1 << ','
          << (info.pps.loop_filter_across_tiles_enabled ? 1 : 0) << '\n';
    }
  }
}

void write_slices_csv(const StreamInfo &info, const std::string &path) {
  std::ofstream out(path);
  if (!out) throw Error("cannot write " + path);
  out << "slice_nal,nal_type,slice_type,first_slice,slice_address,poc_lsb,"
         "header_bits,data_offset_bytes,substream_bytes,entry_index,"
         "entry_offset_minus1,entry_bytes\n";
  for (const SliceInfo &slice : info.slices) {
    if (slice.entry_points.empty()) {
      out << slice.nal_index << ',' << nal_type_name(slice.nal_type) << ','
          << slice_type_name(slice.slice_type) << ','
          << (slice.first_slice_segment_in_pic ? 1 : 0) << ','
          << slice.slice_segment_address << ',' << slice.pic_order_cnt_lsb
          << ',' << slice.header_bits << ',' << slice.data_offset_bits / 8
          << ',' << slice.substream_bytes << ",,,,\n";
      continue;
    }
    for (const EntryPointInfo &entry : slice.entry_points) {
      out << slice.nal_index << ',' << nal_type_name(slice.nal_type) << ','
          << slice_type_name(slice.slice_type) << ','
          << (slice.first_slice_segment_in_pic ? 1 : 0) << ','
          << slice.slice_segment_address << ',' << slice.pic_order_cnt_lsb
          << ',' << slice.header_bits << ',' << slice.data_offset_bits / 8
          << ',' << slice.substream_bytes << ',' << entry.index << ','
          << entry.offset_minus1 << ',' << entry.bytes << '\n';
    }
  }
}

}  // namespace hevclab

