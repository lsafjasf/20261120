"""Test bootstrap for the optional repository-local dependency directory."""

import sys
from pathlib import Path

local_dependencies = Path(__file__).resolve().parent / ".deps"
if local_dependencies.is_dir():
    sys.path.insert(0, str(local_dependencies))
