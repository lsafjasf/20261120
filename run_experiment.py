"""Generate all reverb experiments and artifacts."""

import sys
from pathlib import Path

local_dependencies = Path(__file__).resolve().parent / ".deps"
if local_dependencies.is_dir():
    sys.path.insert(0, str(local_dependencies))

from fdn_reverb.experiment import run_all


if __name__ == "__main__":
    summary = run_all()
    print(
        "all_required_checks_passed:",
        summary["all_required_checks_passed"],
    )
