#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/tools/ffmpeg-7.0.2-amd64-static:$PATH"
W=960; H=540; FPS=30
RAW=work/source_${W}x${H}_${FPS}.yuv
BASE=(-f rawvideo -pix_fmt yuv420p -s ${W}x${H} -r $FPS -i "$RAW" -an -sn -dn)
COMMON=(-preset medium -g 60 -keyint_min 60 -sc_threshold 0 -pix_fmt yuv420p -benchmark)
mkdir -p work/enc work/logs
# name | extra x264/ffmpeg rate-control args
declare -A ARGS
ARGS[crf]="       -crf 23"
ARGS[abr]="       -b:v 2500k"
ARGS[vbv_native]="-crf 23 -maxrate 4000k -bufsize 8000k"
ARGS[crf_cap]="  -crf 23 -maxrate 4000k -bufsize 8000k"
ARGS[abr_cap]="  -b:v 2500k -maxrate 4000k -bufsize 8000k"
ARGS[vbv_tight]=" -crf 23 -maxrate 4000k -bufsize 4000k"
for name in crf abr vbv_native crf_cap abr_cap vbv_tight; do
  for rep in 1 2 3; do
    out="work/enc/${name}.r${rep}.mp4"
    log="work/logs/${name}.r${rep}.log"
    /usr/bin/time -f 'WALL_ELAPSED=%e USER_CPU=%U SYS_CPU=%S' -o "work/logs/${name}.r${rep}.time" \
      ffmpeg -hide_banner -loglevel debug "${BASE[@]}" -c:v libx264 "${COMMON[@]}" ${ARGS[$name]} \
        -y "$out" 2> "$log"
  done
  echo "done $name"
done
ls -la work/enc | head -20
