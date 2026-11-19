#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KVAZAAR_BIN="${KVAZAAR_BIN:-$ROOT/.deps/kvazaar/src/kvazaar}"
FFMPEG_BIN="${FFMPEG_BIN:-$ROOT/.deps/ffmpeg/ffmpeg}"
LAB="$ROOT/build/hevc-lab"
WORK="$ROOT/work"
RESULTS="$ROOT/results"

[[ -x "$KVAZAAR_BIN" ]] || { echo "missing KVAZAAR_BIN=$KVAZAAR_BIN" >&2; exit 1; }
[[ -x "$FFMPEG_BIN" ]] || { echo "missing FFMPEG_BIN=$FFMPEG_BIN" >&2; exit 1; }
[[ -x "$LAB" ]] || { echo "run scripts/build.sh first" >&2; exit 1; }

mkdir -p "$WORK" "$RESULTS" "$RESULTS/kvazaar-ctu"

"$LAB" gen-tile \
  --out "$WORK/tile_stress_1024x512_10b_420.yuv" \
  --width 1024 --height 512 --frames 4 --fps 25
"$LAB" gen-subband \
  --out "$WORK/high_subband_1024x512_10b_420.yuv" \
  --width 1024 --height 512 --frames 4 --fps 25

encode_tiles() {
  local layout="$1"
  local target="$2"
  local stats="$RESULTS/kvazaar-ctu/tiles_${layout}/"
  mkdir -p "$stats"
  "$KVAZAAR_BIN" \
    -i "$WORK/tile_stress_1024x512_10b_420.yuv" \
    --input-res 1024x512 --input-format P420 \
    --input-bitdepth 10 --output-bitdepth 10 \
    -o "$RESULTS/tiles_${layout}.hevc" -n 4 --period 1 --gop 0 \
    --bitrate "$target" --rc-algorithm lambda --preset medium \
    --tiles "$layout" --stats-file-prefix "$stats" \
    > "$WORK/encode_${layout}.log" 2>&1
  "$FFMPEG_BIN" -hide_banner -loglevel error -f hevc \
    -i "$RESULTS/tiles_${layout}.hevc" \
    -pix_fmt yuv420p10le -f rawvideo \
    -y "$RESULTS/tiles_${layout}.dec.yuv"
  "$LAB" analyze --input "$RESULTS/tiles_${layout}.hevc" \
    --ctu-csv "${stats}ctu.csv" --prefix "$RESULTS/tiles_${layout}" \
    > "$WORK/analyze_${layout}.txt"
  "$LAB" metrics-boundary \
    --source "$WORK/tile_stress_1024x512_10b_420.yuv" \
    --decoded "$RESULTS/tiles_${layout}.dec.yuv" \
    --boundaries "$RESULTS/tiles_${layout}.boundaries.csv" \
    --width 1024 --height 512 --frames 4 --bit-depth 10 \
    --frame-csv "$RESULTS/tiles_${layout}.frames.csv" \
    --seam-csv "$RESULTS/tiles_${layout}.seams.csv"
}

encode_tiles 1x1 2180000
encode_tiles 2x1 2000000
encode_tiles 1x2 2000000
encode_tiles 4x1 2000000
encode_tiles 2x2 2000000
encode_tiles 4x2 2000000

for qp in 18 24 32; do
  "$KVAZAAR_BIN" \
    -i "$WORK/high_subband_1024x512_10b_420.yuv" \
    --input-res 1024x512 --input-format P420 \
    --input-bitdepth 10 --output-bitdepth 10 \
    -o "$WORK/high_subband_q${qp}.hevc" -n 4 --period 1 --gop 0 \
    --qp "$qp" --preset medium --tiles 2x2 \
    > "$WORK/hs${qp}.log" 2>&1
  "$FFMPEG_BIN" -hide_banner -loglevel error -f hevc \
    -i "$WORK/high_subband_q${qp}.hevc" \
    -pix_fmt yuv420p10le -f rawvideo -y "$WORK/hs${qp}.dec.yuv"
  "$LAB" metrics-subband \
    --source "$WORK/high_subband_1024x512_10b_420.yuv" \
    --decoded "$WORK/hs${qp}.dec.yuv" \
    --width 1024 --height 512 --frames 4 --bit-depth 10 \
    --csv "$WORK/hs${qp}.csv"
done

cp "$WORK/high_subband_q32.hevc" "$RESULTS/high_subband_2x2.hevc"
cp "$WORK/hs32.dec.yuv" "$RESULTS/high_subband_2x2.dec.yuv"
cp "$WORK/hs32.csv" "$RESULTS/high_subband_2x2.subband.csv"
"$LAB" analyze --input "$RESULTS/high_subband_2x2.hevc" \
  --prefix "$RESULTS/high_subband_2x2" \
  > "$WORK/analyze_high_subband.txt"

"$ROOT/scripts/aggregate.sh"
