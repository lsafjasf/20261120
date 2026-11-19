#!/usr/bin/env bash
# End-to-end pipeline: EQ analysis, subtitle wrap analysis and unit tests.
set -euo pipefail

cd "$(dirname "$0")"

# User-site packages live under the real home when HOME is redirected.
USER_SITE="/home/hj/.local/lib/python3.14/site-packages"
export PYTHONPATH="${USER_SITE}:${PYTHONPATH:-}:."

echo "== [1/3] parametric EQ analysis =="
python3 -m eq.run_analysis >/dev/null

echo "== [2/3] subtitle resize/wrap analysis =="
python3 -m subtitle.run_resize >/dev/null

echo "== [3/3] unit tests =="
python3 tests/test_eq.py
python3 tests/test_wrap.py

echo
echo "All artifacts written to ./outputs"
ls -1 outputs
