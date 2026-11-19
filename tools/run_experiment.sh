#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VVENC="${VVENC_BIN:-}"
VVDEC="${VVDEC_BIN:-}"

if [[ -z "$VVENC" || -z "$VVDEC" ]]; then
  echo "Set VVENC_BIN and VVDEC_BIN to the vvencapp and vvdecapp executables." >&2
  exit 2
fi

cd "$ROOT"
mkdir -p samples/raw samples/streams samples/decoded samples/report samples/audio

python3 tools/generate_yuv.py \
  --width 96 --height 64 --bit-depth 8 --frames 12 \
  --output samples/raw/low_96x64_8bit.yuv
python3 tools/generate_yuv.py \
  --width 192 --height 128 --bit-depth 10 --frames 12 \
  --output samples/raw/high_192x128_10bit.yuv

"$VVENC" -i samples/raw/low_96x64_8bit.yuv -s 96x64 -c yuv420 \
  -r 24 -f 12 -o samples/streams/low_96x64_8bit.vvc \
  --preset faster --qp 28 --refreshsec 100 --intraperiod 100 \
  --refreshtype idr --internal-bitdepth 8 -t 4 --stats 0 -v 4 \
  > samples/report/encode_low.log 2>&1
"$VVENC" -i samples/raw/high_192x128_10bit.yuv -s 192x128 \
  -c yuv420_10 -r 24 -f 12 -o samples/streams/high_192x128_10bit.vvc \
  --preset faster --qp 28 --refreshsec 100 --intraperiod 100 \
  --refreshtype idr -t 4 --stats 0 -v 4 \
  > samples/report/encode_high.log 2>&1

python3 - <<'PY'
from pathlib import Path
low = Path("samples/streams/low_96x64_8bit.vvc").read_bytes()
high = Path("samples/streams/high_192x128_10bit.vvc").read_bytes()
Path("samples/streams/switch_low_high_low.vvc").write_bytes(low + high + low)
PY

LOW_SIZE="$(stat -c%s samples/streams/low_96x64_8bit.vvc)"
HIGH_SIZE="$(stat -c%s samples/streams/high_192x128_10bit.vvc)"
python3 tools/build_unknown_probe.py \
  --base samples/streams/switch_low_high_low.vvc \
  --output samples/streams/switch_with_unknown.vvc \
  --low-size "$LOW_SIZE" --high-size "$HIGH_SIZE"

"$VVDEC" -b samples/streams/low_96x64_8bit.vvc \
  -o samples/decoded/low_96x64_8bit_ref.yuv -v 4 \
  > samples/report/decode_low_ref.log 2>&1
"$VVDEC" -b samples/streams/high_192x128_10bit.vvc \
  -o samples/decoded/high_192x128_10bit_ref.yuv -v 4 \
  > samples/report/decode_high_ref.log 2>&1

python3 tools/generate_band_switch.py --output-dir samples/audio \
  > samples/report/band_switch_stdout.json
python3 tools/run_validation.py --vvdec "$VVDEC" \
  > samples/report/validation_stdout.json
python3 tools/render_report.py
python3 tools/inspect_vvc.py samples/streams/vps_sps_pps_multires.vvc \
  --json samples/report/vps_sample_analysis.json > /dev/null
python3 tools/inspect_vvc.py samples/streams/switch_low_high_low.vvc \
  --json samples/report/switch_stream_analysis.json > /dev/null
python3 tools/inspect_vvc.py samples/streams/switch_with_unknown.vvc \
  --unknown-dir samples/unknown_raw \
  --json samples/report/unknown_nal_analysis.json > /dev/null
python3 -m unittest discover -s tests -v
