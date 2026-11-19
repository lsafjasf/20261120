#!/usr/bin/env bash
# Reproduce the full AGC experiment and coordinate checks.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/.deps:$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m agc_demo.main
python3 -m pytest tests/ -q
