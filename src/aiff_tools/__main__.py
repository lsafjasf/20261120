"""Command-line entry point for regenerating all evidence artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import build_artifacts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="workspace root (default: current directory)")
    parser.add_argument(
        "--no-generate-fixtures",
        action="store_true",
        help="reuse existing fixtures instead of regenerating them",
    )
    args = parser.parse_args()
    measurements = build_artifacts(args.root, generate=not args.no_generate_fixtures)
    summary = {
        "payload_complete": all(
            entry.get("data_validation", {}).get("payload_complete", False)
            for entry in measurements["inspection"].values()
        ),
        "traceability": all(item["reversible"] for item in measurements["traceability"]),
        "seam": measurements["seam"]["summary"],
        "timestretch": {
            key: measurements["timestretch"][key]
            for key in (
                "output_frames",
                "target_frames",
                "click_count",
                "max_boundary_phase_jump_deg",
                "phase_continuous",
                "no_clicks",
                "accepted",
            )
        },
        "artifacts": str(Path(args.root).resolve() / "artifacts"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if (
        summary["payload_complete"]
        and summary["traceability"]
        and summary["seam"]["seamless_edit_is_seamless"]
        and summary["seam"]["hard_cut_detected_as_click"]
        and summary["timestretch"]["accepted"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
