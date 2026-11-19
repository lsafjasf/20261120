#!/bin/bash
# 生成测试用 FLV/MP4 素材（H.264 + AAC，固定帧率，固定 GOP 间隔）。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"

FPS=25
GOP=50

# 1) 先生成 MP4：25fps、每 50 帧一个 GOP（2s）、AAC 44.1kHz 立体声。
#    testsrc 带时间码便于肉眼核对帧号；anoisesrc 提供固定采样率音频。
ffmpeg -y -loglevel error \
  -f lavfi -i "testsrc2=size=320x240:rate=${FPS}:duration=6" \
  -f lavfi -i "anoisesrc=sample_rate=44100:amplitude=0.3:duration=6" \
  -vf "drawtext=text='%{n}':x=10:y=10:fontsize=36:fontcolor=white" \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p \
  -g ${GOP} -keyint_min ${GOP} -sc_threshold 0 -bf 0 \
  -c:a aac -ar 44100 -ac 2 -b:a 64k \
  -movflags +faststart \
  "${HERE}/src.mp4"

# 2) 重封装为 FLV（copy 流，时间基由 ffmpeg 按 FLV 规则写入）。
ffmpeg -y -loglevel error -i "${HERE}/src.mp4" \
  -c copy -f flv "${HERE}/sample.flv"

echo "fixtures ready:"
ls -l "${HERE}/src.mp4" "${HERE}/sample.flv"

# 3) 第二个素材：30fps、含 B 帧（产生非零 composition time）、
#    GOP=60（2s）、单声道 AAC，用于检验 CTS 与不同时间基的处理。
ffmpeg -y -loglevel error \
  -f lavfi -i "testsrc2=size=176x144:rate=30:duration=4" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=4" \
  -c:v libx264 -preset veryfast -pix_fmt yuv420p \
  -g 60 -keyint_min 60 -sc_threshold 0 -bf 2 \
  -c:a aac -ar 48000 -ac 1 -b:a 48k \
  -movflags +faststart \
  "${HERE}/src2.mp4"

ffmpeg -y -loglevel error -i "${HERE}/src2.mp4" \
  -c copy -f flv "${HERE}/sample2.flv"

ls -l "${HERE}/src2.mp4" "${HERE}/sample2.flv"
