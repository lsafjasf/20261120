#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/tools/ffmpeg-7.0.2-amd64-static:$PATH"
RAW=work/source_960x540_30.yuv
mkdir -p work/yuv work/metrics
for name in crf abr vbv_native crf_cap abr_cap vbv_tight; do
  mp4="work/enc/${name}.r1.mp4"
  # decode to raw yuv (loopback)
  ffmpeg -hide_banner -loglevel error -i "$mp4" -pix_fmt yuv420p -f rawvideo -y "work/yuv/${name}.yuv"
  # PSNR + SSIM against the original raw source
  ffmpeg -hide_banner -loglevel info \
    -f rawvideo -pix_fmt yuv420p -s 960x540 -r 30 -i "$RAW" \
    -i "$mp4" -lavfi "psnr=stats_file=work/metrics/${name}.psnr.csv:stats_version=2" \
    -f null - 2> "work/metrics/${name}.psnr.log"
  ffmpeg -hide_banner -loglevel info \
    -f rawvideo -pix_fmt yuv420p -s 960x540 -r 30 -i "$RAW" \
    -i "$mp4" -lavfi "ssim=stats_file=work/metrics/${name}.ssim.csv" \
    -f null - 2> "work/metrics/${name}.ssim.log"
  echo "metrics done: $name"
done
grep -H "PSNR" work/metrics/*.psnr.log | sed 's#work/metrics/##'
grep -H "SSIM" work/metrics/*.ssim.log | sed 's#work/metrics/##'
