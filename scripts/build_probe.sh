#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BUILD="$ROOT/.build"
SPEEX_REV=${SPEEX_REV:-09b569d1b437e5edccf3406a38714ad795e46d38}
SPEEX_SRC=${SPEEX_SRC:-"$BUILD/speex-1.2.1"}

mkdir -p "$BUILD"
mkdir -p "$ROOT/artifacts"
if [[ ! -f "$SPEEX_SRC/libspeex/speex.c" ]]; then
  git clone --depth 1 https://github.com/xiph/speex.git "$SPEEX_SRC"
fi
if git -C "$SPEEX_SRC" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$SPEEX_SRC" fetch --depth 1 origin "$SPEEX_REV" >/dev/null 2>&1 || true
  git -C "$SPEEX_SRC" checkout --detach "$SPEEX_REV" >/dev/null 2>&1 || true
fi

mapfile -t SOURCES < <(
  find "$SPEEX_SRC/libspeex" -maxdepth 1 -name '*.c' \
    ! -name 'testenc*.c' ! -name 'vorbis_psy.c' -printf '%p\n' | sort
)

cc -std=c99 -O2 -DHAVE_CONFIG_H \
  -I"$ROOT/compat" -I"$ROOT/compat/speex" \
  -I"$SPEEX_SRC/include" -I"$SPEEX_SRC/libspeex" \
  "$ROOT/src/speex_probe.c" "${SOURCES[@]}" \
  -Wl,--wrap=lsp_to_lpc \
  -Wl,--wrap=signal_mul \
  -Wl,--wrap=iir_mem16 \
  -Wl,--wrap=split_cb_shape_sign_unquant \
  -lm -o "$BUILD/speex_probe"

"$BUILD/speex_probe" "$ROOT/artifacts"
