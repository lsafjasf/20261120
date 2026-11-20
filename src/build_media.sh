#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/artifacts/media"
mkdir -p "$OUT"
PEAK_KBPS=${PEAK_KBPS:-800}
ABR_KBPS=${ABR_KBPS:-500}
FORCE=${FORCE:-0}

FFMPEG=${FFMPEG:-}
if [[ -z "$FFMPEG" ]]; then
  for candidate in \
    /tmp/ffmpeg-7.0.2-amd64-static/ffmpeg \
    "$ROOT/.tools/ffmpeg/ffmpeg" \
    "$(command -v ffmpeg || true)"; do
    if [[ -x "$candidate" ]] && "$candidate" -version >/dev/null 2>&1; then
      FFMPEG=$candidate
      break
    fi
  done
fi
if [[ -z "$FFMPEG" ]]; then
  echo "ffmpeg not found; set FFMPEG=/path/to/ffmpeg" >&2
  exit 1
fi

ENCODE_OPTS=(
  -an -c:v libx264 -preset medium -pix_fmt yuv420p
  -g 12 -keyint_min 12 -sc_threshold 0 -bf 0
  -x264-params "open-gop=0:bframes=0"
)

if [[ ! -f "$OUT/still.png" || "$FORCE" == 1 ]]; then
  "$FFMPEG" -hide_banner -y -loglevel error \
    -f lavfi -i "testsrc2=size=640x360:rate=24:duration=0.041667" \
    -frames:v 1 "$OUT/still.png"
fi

if [[ ! -f "$OUT/source.mp4" || "$FORCE" == 1 ]]; then
  "$FFMPEG" -hide_banner -y -loglevel warning \
    -loop 1 -framerate 24 -i "$OUT/still.png" \
    -f lavfi -i "testsrc2=size=640x360:rate=24:duration=6" \
    -loop 1 -framerate 24 -i "$OUT/still.png" \
    -filter_complex \
      "[0:v]trim=duration=3,setpts=PTS-STARTPTS[s1];\
       [1:v]trim=duration=6,setpts=PTS-STARTPTS[m];\
       [2:v]trim=duration=3,setpts=PTS-STARTPTS[s2];\
       [s1][m][s2]concat=n=3:v=1:a=0,format=yuv420p[v]" \
    -map "[v]" -r 24 -an -c:v libx264 -preset medium -qp 0 \
    -g 12 -keyint_min 12 -sc_threshold 0 -movflags +faststart \
    "$OUT/source.mp4"
fi

# All three rate-control modes share maxrate and VBV buffer size.
if [[ ! -f "$OUT/cbr.mp4" || "$FORCE" == 1 ]]; then
  "$FFMPEG" -hide_banner -y -loglevel warning -i "$OUT/source.mp4" \
    "${ENCODE_OPTS[@]}" \
    -b:v "${PEAK_KBPS}k" -minrate "${PEAK_KBPS}k" \
    -maxrate "${PEAK_KBPS}k" -bufsize "${PEAK_KBPS}k" \
    -x264-params "nal-hrd=cbr:force-cfr=1:open-gop=0:bframes=0" \
    "$OUT/cbr.mp4"
fi

if [[ ! -f "$OUT/abr.mp4" || "$FORCE" == 1 ]]; then
  "$FFMPEG" -hide_banner -y -loglevel warning -i "$OUT/source.mp4" \
    "${ENCODE_OPTS[@]}" \
    -b:v "${ABR_KBPS}k" -maxrate "${PEAK_KBPS}k" \
    -bufsize "${PEAK_KBPS}k" \
    "$OUT/abr.mp4"
fi

if [[ ! -f "$OUT/vbv.mp4" || "$FORCE" == 1 ]]; then
  "$FFMPEG" -hide_banner -y -loglevel warning -i "$OUT/source.mp4" \
    "${ENCODE_OPTS[@]}" \
    -crf 12 -maxrate "${PEAK_KBPS}k" -bufsize "${PEAK_KBPS}k" \
    -movflags "+empty_moov+default_base_moof+frag_keyframe" \
    -min_frag_duration 500000 -frag_duration 500000 \
    "$OUT/vbv.mp4"
fi

printf 'ffmpeg=%s\n' "$FFMPEG"
