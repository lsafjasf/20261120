"""Emit the key-status player behaviour table as CSV and JSON."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.key_status import KEY_STATUS_ROWS  # noqa: E402

OUT = ROOT / "outputs"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    fields = [
        "status",
        "playback",
        "playerState",
        "pause",
        "error",
        "action",
        "message",
    ]
    with (OUT / "key_status.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(KEY_STATUS_ROWS)
    (OUT / "key_status.json").write_text(
        json.dumps(KEY_STATUS_ROWS, indent=2, ensure_ascii=False)
    )
    for row in KEY_STATUS_ROWS:
        print(
            f"{row['status']:18s} {row['playback']:9s} "
            f"pause={row['pause']!s:<5} error={row['error']}"
        )


if __name__ == "__main__":
    main()
