#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/.build"
SOURCE="$BUILD/opencore-amr"
COMMIT="3b67218fb8efb776bcd79e7445774e02d778321d"

mkdir -p "$BUILD"
if [[ ! -d "$SOURCE/.git" ]]; then
  git clone https://github.com/BelledonneCommunications/opencore-amr.git "$SOURCE"
fi
git -C "$SOURCE" fetch --quiet origin "$COMMIT"
git -C "$SOURCE" checkout --quiet "$COMMIT"

AMR_BASE="$SOURCE/opencore/codecs_v2/audio/gsm_amr"
DEC_SRC="$AMR_BASE/amr_nb/dec/src"
ENC_SRC="$AMR_BASE/amr_nb/enc/src"
COMMON_SRC="$AMR_BASE/amr_nb/common/src"

mapfile -t SOURCES < <(
  find "$COMMON_SRC" "$DEC_SRC" "$ENC_SRC" -maxdepth 1 -name '*.cpp' |
  grep -Ev "/common/src/(bits2prm|copy|div_32|l_abs|r_fft|vad1|vad2)\.cpp$" |
  grep -Ev '/(decoder_gsm_amr|pvgsmamrdecoder|gsmamr_encoder_wrapper)\.cpp$'
)

g++ -O2 -std=c++17 -w \
  -I"$SOURCE/oscl" \
  -I"$SOURCE/amrnb" \
  -I"$AMR_BASE/amr_nb/common/include" \
  -I"$DEC_SRC" -I"$ENC_SRC" \
  -I"$AMR_BASE/amr_nb/dec/include" \
  -I"$AMR_BASE/common/dec/include" \
  "$SOURCE/amrnb/wrapper.cpp" \
  "${SOURCES[@]}" \
  "$ROOT/src/amr_nb_probe.cpp" \
  -Wl,--wrap=Syn_filt \
  -Wl,--wrap=Int_lpc_1to3 \
  -Wl,--wrap=Int_lpc_1and3 \
  -o "$BUILD/amr_nb_probe"

printf 'built %s at OpenCore %s\n' "$BUILD/amr_nb_probe" "$COMMIT"
