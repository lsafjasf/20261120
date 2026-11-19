#!/usr/bin/env bash
# Reproduce every output under outputs/ from the media in artifacts/media.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# numpy/scipy are expected in a local .deps directory (see README).
DEPS_CANDIDATES=(
  "$ROOT/.deps"
  "$ROOT/../uid4-0037/.deps"
)
for candidate in "${DEPS_CANDIDATES[@]}"; do
  if [ -d "$candidate/numpy" ]; then
    export PYTHONPATH="$candidate${PYTHONPATH:+:$PYTHONPATH}"
    break
  fi
done

python3 scripts/score_all.py
python3 scripts/compare_ranks.py
python3 scripts/key_status_table.py
python3 scripts/build_report.py
python3 -m pytest tests/ -q
