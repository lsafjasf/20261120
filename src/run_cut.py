"""Run both export paths on the same frame range and save a run manifest."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smartcut import smart_cut, stream_copy_cut

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "media" / "source.mp4"
SRT = ROOT / "media" / "source.srt"
OUTDIR = ROOT / "output"

# deliberately inside GOP 1 (45) and inside GOP 7 (214): both edit
# points are non-keyframe, forcing two local re-encode windows
START, END = 45, 215


def main() -> None:
    copy_out = stream_copy_cut(SRC, START, END, OUTDIR, "streamcopy", SRT)
    smart_out, segments = smart_cut(SRC, START, END, OUTDIR, "smartcut", SRT)
    manifest = {
        "source": str(SRC.relative_to(ROOT)),
        "cut": {"start_frame": START, "end_frame_exclusive": END,
                "expected_frames": END - START},
        "streamcopy_output": str(copy_out.relative_to(ROOT)),
        "smartcut_output": str(smart_out.relative_to(ROOT)),
        "segments": [
            {
                "kind": s.kind,
                "file": str(s.path.relative_to(ROOT)),
                "src_gop_frames": [s.src_start, s.src_end],
                "kept_frames": [s.keep_start, s.keep_end],
                "reencoded_frames": s.encoded_frames,
            }
            for s in segments
        ],
    }
    dest = ROOT / "reports" / "run_manifest.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
