"""Command-line entry: inspect / audition / gencases / verify."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from . import audio, coverage, gencases
from .audition import Engine
from .keyboard import describe, run_keyboard
from .sfzparse import midi_to_name, note_to_midi, parse_sfz

VEL_STEPS = (1, 32, 63, 64, 96, 127)


def cmd_inspect(args):
    sfz = Path(args.sfz)
    regions = parse_sfz(sfz)
    print(f"{sfz.name}: {len(regions)} regions")
    for r in regions:
        print(f"  L{r.line:<3} {r.sample or '(no sample)':<22} "
              f"keys {midi_to_name(r.lokey)}..{midi_to_name(r.hikey)} "
              f"vel {r.lovel}..{r.hivel} root={midi_to_name(r.root)} "
              f"rr={r.seq_position}/{r.seq_length} "
              f"rand={r.lorand}..{r.hirand}")
    cells, missing = coverage.analyze(regions, sfz.parent, vel_steps=VEL_STEPS)
    print()
    print(coverage.render_matrix(cells, VEL_STEPS))
    gaps = coverage.find_rr_gaps(regions)
    if gaps:
        print("\nround-robin gaps:")
        for g in gaps:
            print(f"  keys {g['keys']} seq_length={g['seq_length']} "
                  f"missing positions {g['missing_positions']}")
    if missing:
        print("\nmissing sample files:")
        for m in missing:
            print(f"  {m}")
    n_conf = sum(1 for c in cells if c.status == coverage.CONFLICT)
    n_unc = sum(1 for c in cells if c.status == coverage.UNCOVERED)
    print(f"\nsummary: {n_unc} uncovered cells, {n_conf} conflict cells, "
          f"{len(missing)} missing files, {len(gaps)} rr gaps")


def cmd_audition(args):
    engine = Engine(args.sfz)
    if args.script:
        run_script(engine, args.script, args.out, args.log)
    else:
        run_keyboard(engine, args.out)


def run_script(engine: Engine, script: str, out_wav: str,
               log_path: str | None):
    """Script lines: '<note|midi> <vel>'; blank/# lines ignored."""
    events_all = []
    rendered: list[list[float]] = []
    for raw in Path(script).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tok = line.split()
        key, vel = note_to_midi(tok[0]), int(tok[1])
        events, buf = engine.trigger(key, vel)
        events_all.extend(events)
        print(describe(events))
        if buf:
            rendered.append(buf)
            rendered.append([0.0] * int(0.08 * audio.SR))
    if rendered:
        audio.write_wav(out_wav, [s for t in rendered for s in t])
        print(f"audition audio -> {out_wav}")
    if log_path:
        export_log(events_all, log_path)
        print(f"trigger log   -> {log_path}")
    return events_all


def export_log(events, path: str):
    path = Path(path)
    rows = [e.as_dict() for e in events]
    if path.suffix == ".csv":
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    else:
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


F0_TOLERANCE_CENTS = 30.0


def cmd_gencases(args):
    out = gencases.generate(args.dir)
    print(f"cases + samples generated in {out}")


def cmd_verify(args):
    """Run the three cases, export trigger lists, check measured f0."""
    case_dir = Path(args.dir)
    if not (case_dir / "case1_velocity_boundary.sfz").exists():
        gencases.generate(case_dir)
    rep = Path(args.report_dir)
    rep.mkdir(parents=True, exist_ok=True)
    scripts = {
        "case1_velocity_boundary": [(64, 1), (64, 63), (64, 64), (64, 127)],
        "case2_roundrobin_gap": [(60, 100)] * 6,
        "case3_semitone_transpose": [(k, 100) for k in
                                     (55, 56, 57, 58, 59, 60, 61, 62, 63, 64)],
    }
    all_ok = True
    for name, hits in scripts.items():
        print(f"\n=== {name} ===")
        engine = Engine(case_dir / f"{name}.sfz")
        script = case_dir / f"{name}.triggers"
        script.write_text("".join(f"{k} {v}\n" for k, v in hits))
        events = run_script(engine, str(script), str(rep / f"{name}.wav"),
                            str(rep / f"{name}.json"))
        export_log(events, str(rep / f"{name}.csv"))
        for e in events:
            if e.sample and e.file_exists and e.f0_cents_err is not None:
                ok = abs(e.f0_cents_err) <= F0_TOLERANCE_CENTS
                all_ok &= ok
                if not ok:
                    print(f"  F0 MISMATCH #{e.seq}: {e.f0_cents_err:+.1f} cents")
        if name == "case1_velocity_boundary":
            got = [e.sample for e in events]
            want = ["soft_c4.wav", "soft_c4.wav", "hard_c4.wav", "hard_c4.wav"]
            ok = got == want
            all_ok &= ok
            print(f"  layer pick {'OK' if ok else f'FAIL {got}'}")
        if name == "case2_roundrobin_gap":
            rr = [(e.sample, e.rr_position) for e in events]
            expect = [("rr1_c4.wav", 1), ("rr2_c4.wav", 2), ("", 3),
                      ("rr4_c4.wav", 4), ("rr1_c4.wav", 1), ("rr2_c4.wav", 2)]
            ok = rr == expect
            all_ok &= ok
            print(f"  rr sequence {'OK (gap at slot 3 detected)' if ok else f'FAIL {rr}'}")
    print(f"\nverify {'PASSED' if all_ok else 'FAILED'}; reports in {rep}")
    return 0 if all_ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sfzbench",
                                 description="SFZ pre-release inspection bench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="coverage matrix + conflict/missing report")
    p.add_argument("sfz")
    p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("audition", help="audition keyboard or scripted triggers")
    p.add_argument("sfz")
    p.add_argument("--script", help="trigger list file: '<note> <vel>' per line")
    p.add_argument("--out", default="audition.wav")
    p.add_argument("--log", help="trigger log export (.json or .csv)")
    p.set_defaults(fn=cmd_audition)
    p = sub.add_parser("gencases", help="generate samples + 3 validation cases")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_gencases)
    p = sub.add_parser("verify", help="run cases, export logs, measure f0")
    p.add_argument("dir")
    p.add_argument("--report-dir", default="reports")
    p.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
