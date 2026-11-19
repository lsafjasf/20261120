#!/usr/bin/env bash
set -euo pipefail
export PATH="$PWD/tools/ffmpeg-7.0.2-amd64-static:$PATH"
W=960; H=540; FPS=30
RAW=work/source_${W}x${H}_${FPS}.yuv
# 5 segments (seconds): calm, burst(noise), calm2, gradient, burst2 -> 3+4+3+4+4 = 18s
seg () { # name duration filter
  ffmpeg -hide_banner -loglevel error -f lavfi -i "testsrc2=size=${W}x${H}:rate=${FPS}:duration=$2" \
    -vf "$3" -pix_fmt yuv420p -f rawvideo -y "work/t/src_$1.yuv"
}
seg 01 3 "null"
seg 02 4 "noise=alls=40:allf=t+u"
seg 03 3 "null"
seg 04 4 "hue=H=2*PI*t/4:s=1.2,drawgrid=w=80:h=80:t=2:c=white@0.5,eq=brightness=0.02"
seg 05 4 "noise=alls=55:allf=t+u,eq=contrast=1.3"
cat work/t/src_01.yuv work/t/src_02.yuv work/t/src_03.yuv work/t/src_04.yuv work/t/src_05.yuv > "$RAW"
ls -la "$RAW"
python3 - <<PY
import os
fs=os.path.getsize("$RAW"); frame=960*540*3//2
print("frames expected:", fs//frame, "bytes/frame:", frame)
PY
# also make an mkv reference for convenience / ffprobe
ffmpeg -hide_banner -loglevel error -f rawvideo -pix_fmt yuv420p -s ${W}x${H} -r $FPS -i "$RAW" -c:v ffv1 -y work/source_ref.mkv
ffprobe -v error -count_frames -select_streams v -show_entries stream=nb_read_frames,r_frame_rate -of csv work/source_ref.mkv
