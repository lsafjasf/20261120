#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEBS="$ROOT/.deps/debs"
PREFIX="$ROOT/.deps/usr"
mkdir -p "$DEBS" "$PREFIX" "$ROOT/bin"

if ! python3 -m pip --version >/dev/null 2>&1; then
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  python3 /tmp/get-pip.py --user --break-system-packages
fi
python3 -m pip install --user --break-system-packages numpy scipy matplotlib pyloudnorm

cd "$DEBS"
if [[ ! -f "$PREFIX/usr/include/webrtc-audio-processing-1/modules/audio_processing/include/audio_processing.h" ]]; then
  apt-get download libwebrtc-audio-processing-dev libwebrtc-audio-processing-1-3 libabsl-dev
fi
cd "$ROOT"
for package in "$DEBS"/*.deb; do
  dpkg-deb -x "$package" "$PREFIX"
done

g++ -O3 -DNDEBUG -std=c++17 \
  "$ROOT/tools/webrtc_denoise.cpp" \
  -I"$PREFIX/usr/include/webrtc-audio-processing-1" \
  -I"$PREFIX/usr/include" \
  -L"$PREFIX/usr/lib/x86_64-linux-gnu" \
  -Wl,-rpath,'$ORIGIN/../.deps/usr/usr/lib/x86_64-linux-gnu' \
  -lwebrtc-audio-processing-1 -pthread \
  -o "$ROOT/bin/webrtc_denoise"

echo "WebRTC bridge: $ROOT/bin/webrtc_denoise"
