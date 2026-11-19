#!/usr/bin/env python3
"""Run the checked-in fixtures and write the requested block-level report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wavpack_blocks import format_report, scan_file  # noqa: E402


CASES = (
    "mono",
    "stereo",
    "hybrid",
    "corrupt_stereo",
    "corrupt_length_stereo",
)


def main() -> int:
    fixtures = ROOT / "fixtures"
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    results = {}

    for name in CASES:
        pcm = (fixtures / f"{name}.decoded.raw").read_bytes()
        results[name] = scan_file(fixtures / f"{name}.wv", pcm)

    clean_indices = results["stereo"].recovered_sample_indices
    damaged = results["corrupt_stereo"]
    broken_length = results["corrupt_length_stereo"]
    bad = next(record for record in damaged.records if not record.accepted)
    later_indices = [
        record.header.block_index
        for record in damaged.records[3:]
        if record.accepted
    ]

    assert damaged.checksum_failures == 1
    assert later_indices == [1536, 1868]
    assert later_indices == clean_indices[3:]
    assert damaged.declared_total_samples == 2200
    assert results["stereo"].declared_total_samples == 2200
    assert damaged.total_samples_consistent
    assert damaged.recovered_sample_span == [0, 2200]
    assert broken_length.records[3].header.offset == damaged.records[3].header.offset

    text = "\n".join(format_report(results[name]).rstrip() for name in CASES)
    text += (
        "\n\nverification:\n"
        f"  clean_sample_indices={clean_indices}\n"
        f"  damaged_block_start=0x{bad.header.offset:X}\n"
        f"  resync_point=0x{bad.resume_offset:X}\n"
        f"  skipped_bytes={bad.skipped_bytes}\n"
        f"  later_sample_indices={later_indices}\n"
        f"  later_indices_match_clean=true\n"
        f"  total_samples_before={results['stereo'].declared_total_samples}\n"
        f"  total_samples_after={damaged.declared_total_samples}\n"
        f"  recovered_sample_span={damaged.recovered_sample_span}\n"
        f"  cumulative_checksum_failures={damaged.checksum_failures}\n"
    )

    (reports / "wavpack_block_report.txt").write_text(text + "\n", encoding="utf-8")
    (reports / "wavpack_block_report.json").write_text(
        json.dumps(
            {name: result.to_dict() for name, result in results.items()},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("verification passed")
    print(
        f"damaged=0x{bad.header.offset:X} "
        f"resync=0x{bad.resume_offset:X} "
        f"later={later_indices} "
        f"total_samples={damaged.declared_total_samples} "
        f"checksum_failures={damaged.checksum_failures}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
