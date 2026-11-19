"""Key x velocity coverage matrix and conflict / missing-sample analysis.

Cell classification:
  UNCOVERED  - no region claims the (key, vel) cell
  OK         - exactly one round-robin layer stack covers it
  CONFLICT   - multiple regions cover the same cell at the same
               seq_position with overlapping lorand/hirand windows,
               i.e. an overlap the author did not declare as a random
               split (undeclared stacking)
  MISSING    - covering region's sample file does not exist on disk
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .sfzparse import Region, midi_to_name

UNCOVERED, OK, CONFLICT, MISSING = "uncovered", "ok", "conflict", "missing"


@dataclass
class CellReport:
    key: int
    vel: int
    status: str
    regions: list[Region]


def regions_at(regions: list[Region], key: int, vel: int) -> list[Region]:
    return [r for r in regions if r.covers(key, vel)]


def is_conflict(group: list[Region]) -> bool:
    """True when regions stack at one cell without a declared random split."""
    seen: dict[int, list[Region]] = {}
    for r in group:
        seen.setdefault(r.seq_position, []).append(r)
    for stack in seen.values():
        if len(stack) < 2:
            continue
        for i in range(len(stack)):
            for j in range(i + 1, len(stack)):
                a, b = stack[i], stack[j]
                if a.lorand < b.hirand and b.lorand < a.hirand:
                    return True
    return False


def sample_path(sfz_dir: Path, region: Region) -> Path:
    return sfz_dir / region.sample.replace("\\", "/")


def find_rr_gaps(regions: list[Region]) -> list[dict]:
    """Detect missing seq_position numbers inside round-robin groups."""
    groups: dict[tuple, set[int]] = {}
    for r in regions:
        if r.seq_length > 1:
            key = (r.lokey, r.hikey, r.lovel, r.hivel, r.seq_length)
            groups.setdefault(key, set()).add(r.seq_position)
    gaps = []
    for (lokey, hikey, lovel, hivel, seq_len), positions in groups.items():
        missing = [p for p in range(1, seq_len + 1) if p not in positions]
        if missing:
            gaps.append({"keys": f"{lokey}-{hikey}", "vels": f"{lovel}-{hivel}",
                         "seq_length": seq_len, "missing_positions": missing})
    return gaps


def analyze(regions: list[Region], sfz_dir: str | Path,
            lokey: int | None = None, hikey: int | None = None,
            vel_steps: tuple[int, ...] = (1, 32, 64, 96, 127)):
    """Sweep the key/vel space and classify every cell."""
    sfz_dir = Path(sfz_dir)
    if lokey is None:
        lokey = min(r.lokey for r in regions)
    if hikey is None:
        hikey = max(r.hikey for r in regions)
    missing_files = sorted({
        r.sample for r in regions
        if r.sample and not sample_path(sfz_dir, r).exists()
    })
    cells: list[CellReport] = []
    for key in range(lokey, hikey + 1):
        for vel in vel_steps:
            group = regions_at(regions, key, vel)
            if not group:
                status = UNCOVERED
            elif any(r.sample in missing_files for r in group):
                status = MISSING
            elif is_conflict(group):
                status = CONFLICT
            else:
                status = OK
            cells.append(CellReport(key, vel, status, group))
    return cells, missing_files


SYMBOLS = {UNCOVERED: ".", OK: "o", CONFLICT: "X", MISSING: "!"}


def render_matrix(cells: list[CellReport], vel_steps: tuple[int, ...]) -> str:
    """ASCII matrix: x = MIDI key, y = velocity (127 at top)."""
    keys = sorted({c.key for c in cells})
    grid = {(c.key, c.vel): c.status for c in cells}
    lines = []
    header1, header2 = "      ", "      "
    for k in keys:
        header1 += str(k // 100 or " ") if k >= 100 else " "
        header2 += str((k // 10) % 10)
    header3 = "      " + "".join(str(k % 10) for k in keys)
    lines += [header1, header2, header3]
    for vel in reversed(vel_steps):
        row = f"v={vel:>3} " + "".join(SYMBOLS[grid[(k, vel)]] for k in keys)
        lines.append(row)
    key_names = "      " + "".join(
        "#" if k % 12 in (1, 3, 6, 8, 10) else "-" for k in keys)
    lines.append(key_names + "  (- white, # black)")
    lines.append(f"keys {keys[0]}..{keys[-1]} "
                 f"({midi_to_name(keys[0])}..{midi_to_name(keys[-1])})")
    lines.append("legend: o covered  . uncovered  X undeclared-overlap conflict  ! missing sample")
    return "\n".join(lines)
