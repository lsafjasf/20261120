#!/usr/bin/env bash
# Replay the full sample: generate subframes, integrate, preview, validate.
set -euo pipefail
cd "$(dirname "$0")"

# numpy / Pillow live in the user site-packages of the real home.
if ! python3 -c "import numpy, PIL" 2>/dev/null; then
  export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/home/hj/.local/lib/python3.14/site-packages"
fi

OUT="${1:-artifacts/demo}"
python3 -m shutter_integrator demo --out "$OUT"
echo "replay complete: $OUT"
