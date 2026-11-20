#!/bin/sh
# 用 libx264 在多个 QP 下分别编码去块开/关两版并解码为 YUV
FF=${FF:-ffmpeg}
mkdir -p enc
for qp in 20 30 40; do
  $FF -y -loglevel error -f rawvideo -pix_fmt yuv420p -s 352x288 -r 30 -i test.yuv \
    -c:v libx264 -qp $qp -x264-params keyint=30:min-keyint=30:scenecut=0 enc/qp${qp}_on.mp4
  $FF -y -loglevel error -f rawvideo -pix_fmt yuv420p -s 352x288 -r 30 -i test.yuv \
    -c:v libx264 -qp $qp -x264-params keyint=30:min-keyint=30:scenecut=0:no-deblock=1 enc/qp${qp}_off.mp4
  $FF -y -loglevel error -i enc/qp${qp}_on.mp4 -f rawvideo -pix_fmt yuv420p enc/qp${qp}_on.yuv
  $FF -y -loglevel error -i enc/qp${qp}_off.mp4 -f rawvideo -pix_fmt yuv420p enc/qp${qp}_off.yuv
done
