#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS="$ROOT/.deps"
KVAZAAR_COMMIT="c997f08c805bd7ec82233654e417e06fe7c83cba"
KVAZAAR_SRC="$DEPS/kvazaar"
KVAZAAR_BUILD="$DEPS/kvazaar-build"
FFMPEG_DIR="$DEPS/ffmpeg"

mkdir -p "$DEPS"
if [[ ! -d "$KVAZAAR_SRC/.git" ]]; then
  git clone https://github.com/ultravideo/kvazaar.git "$KVAZAAR_SRC"
fi
git -C "$KVAZAAR_SRC" fetch --depth 1 origin "$KVAZAAR_COMMIT"
git -C "$KVAZAAR_SRC" checkout --detach "$KVAZAAR_COMMIT"
if git -C "$KVAZAAR_SRC" apply --reverse --check \
    "$ROOT/patches/kvazaar-ctu-csv.patch" 2>/dev/null; then
  :
else
  git -C "$KVAZAAR_SRC" apply "$ROOT/patches/kvazaar-ctu-csv.patch"
fi

cmake -S "$KVAZAAR_SRC" -B "$KVAZAAR_BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTS=OFF \
  -DBUILD_SHARED_LIBS=OFF \
  -DKVZ_BIT_DEPTHS="8;10"
cmake --build "$KVAZAAR_BUILD" -j"$(nproc)"

if [[ ! -x "$FFMPEG_DIR/ffmpeg" ]]; then
  mkdir -p "$FFMPEG_DIR"
  curl -L --fail --retry 2 \
    -o "$DEPS/ffmpeg-static.tar.xz" \
    https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
  tar -xf "$DEPS/ffmpeg-static.tar.xz" -C "$FFMPEG_DIR" --strip-components=1
fi

echo "Kvazaar: $KVAZAAR_SRC/src/kvazaar"
echo "FFmpeg:  $FFMPEG_DIR/ffmpeg"
