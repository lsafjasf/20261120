"""Dual-channel caption priority and overlay-composition rules.

Priority model (highest first):
  1. CEA-708 Service 1  (primary/main caption)
  2. CEA-608 CC1        (main caption when no 708 primary exists)
  3. CEA-708 Service 2+ (secondary)
  4. CEA-608 CC3        (secondary/sub caption on field 2)

Composition rule: the main caption owns the bottom zone (rows 12-15 of
the 15-row 608 grid), the sub caption owns the top zone (rows 1-4).
Z-order: sub is composited first, main on top.  The checker verifies
that simultaneously-visible main/sub events never overlap on screen.
"""

MAIN_ZONE = (66.7, 100.0)    # 608 rows 12-15, percent of screen height
SUB_ZONE = (0.0, 33.3)       # 608 rows 1-4

PRIORITY = {
    "708-S1": 0,
    "608-CC1": 1,
    "608-CC2": 2,
    "708-S2": 3,
    "608-CC3": 4,
    "608-CC4": 5,
}


def source_rank(source):
    return PRIORITY.get(source, 6)


def role_of(source):
    return "main" if source_rank(source) <= 1 else "sub"


def event_region(ev):
    """Normalize any event to (row0%, row1%, col0%, col1%)."""
    if ev.source.startswith("708"):
        r0, r1, c0, c1 = ev.region
        return (r0 / 75 * 100, r1 / 75 * 100, c0 / 210 * 100, c1 / 210 * 100)
    rows = ev.rows or (15,)
    return ((min(rows) - 1) / 15 * 100, max(rows) / 15 * 100, 0.0, 100.0)


def regions_overlap(a, b):
    return not (a[1] <= b[0] or b[1] <= a[0] or a[3] <= b[2] or b[3] <= a[2])


def frames_overlap(a, b):
    a_end = a.end_frame if a.end_frame is not None else float("inf")
    b_end = b.end_frame if b.end_frame is not None else float("inf")
    return a.start_frame <= b_end and b.start_frame <= a_end


def compose(events):
    """Return (z_order, conflicts, relocations, suppressed)."""
    conflicts = []
    relocations = []
    suppressed = []
    for i, ev_a in enumerate(events):
        for ev_b in events[i + 1:]:
            ranks = {source_rank(ev_a.source), source_rank(ev_b.source)}
            if ranks == {0, 1} and frames_overlap(ev_a, ev_b):
                main = ev_a if source_rank(ev_a.source) == 0 else ev_b
                other = ev_b if main is ev_a else ev_a
                suppressed.append(
                    f"{other.source} frames {other.start_frame}-"
                    f"{other.end_frame} suppressed: {main.source} is the "
                    "active primary (708 takes precedence over 608)")
                continue
            if role_of(ev_a.source) == role_of(ev_b.source):
                continue
            if not frames_overlap(ev_a, ev_b):
                continue
            if regions_overlap(event_region(ev_a), event_region(ev_b)):
                main, sub = sorted((ev_a, ev_b),
                                   key=lambda e: source_rank(e.source))
                conflicts.append((main, sub))
                relocations.append(
                    f"move {sub.source} '{sub.text[:20]}' to top zone "
                    f"rows 1-4 while {main.source} occupies bottom zone")
    z_order = sorted(events,
                     key=lambda e: (source_rank(e.source), e.start_frame),
                     reverse=True)
    return z_order, conflicts, relocations, suppressed
