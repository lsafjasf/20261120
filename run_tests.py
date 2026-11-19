"""Run the test suite using either installed or repository-local dependencies."""

import sys
from pathlib import Path

local_dependencies = Path(__file__).resolve().parent / ".deps"
if local_dependencies.is_dir():
    sys.path.insert(0, str(local_dependencies))

import pytest


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", "tests"]))
