"""End-to-end demonstration: conversions, diffs, round-trip error,
bilingual offset preservation and chunk first-frame comparison.

Outputs are written under reports/.
"""

import json
import os

from subtitle_convert import convert
from subtitle_convert.diff import (
    diff_documents, render_unified, max_timeline_drift,
)
from subtitle_convert.fidelity import (
    run_chain, roundtrip_error_table, verify_bilingual_offsets,
)
from net_sim import compare

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "reports")


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def write(path, content):
    with open(os.path.join(OUT, path), "w", encoding="utf-8") as handle:
        handle.write(content)


def conversion_diffs():
    zh = read("samples/bilingual.zh.srt")
    rows = []
    for dst in ("vtt", "ass"):
        dst_text, src_doc, _ = convert.convert(zh, "srt", dst)
        write(f"bilingual.zh.srt.to.{dst}", dst_text)
        reparsed = convert.parse(dst_text, dst)
        result = diff_documents(src_doc, reparsed, "srt", dst)
        unified = render_unified(src_doc, reparsed, "srt", dst)
        write(f"diff_srt_to_{dst}.txt", unified)
        rows.append({
            "pair": f"srt->{dst}",
            "max_timeline_drift_ms": max_timeline_drift(result),
            "text_changed_cues": sum(
                1 for r in result["rows"] if r["text_changed"]),
            "cue_count": result["cue_count_after"],
            "losses": [
                {"scope": l.scope, "name": l.name, "detail": l.detail}
                for l in reparsed.losses
            ],
        })
    return rows


def loss_inventory():
    rich = read("samples/rich.ass")
    doc = convert.parse(rich, "ass")
    inventory = [
        {"scope": l.scope, "feature": l.name, "detail": l.detail,
         "cue_index": l.cue_index}
        for l in doc.losses
    ]
    lines = ["Non-lossless style/feature inventory (rich.ass -> SRT/VTT)",
             "=" * 58]
    for item in inventory:
        cue = (f"event {item['cue_index'] + 1}"
               if item["cue_index"] is not None else "document/style")
        lines.append(f"[{item['scope']:8}] {cue:9} {item['feature']:16} "
                     f"{item['detail']}")
    write("loss_inventory.txt", "\n".join(lines) + "\n")
    return inventory


def roundtrips():
    srt_seed = read("samples/bilingual.zh.srt")
    ass_seed = read("samples/rich.ass")
    quant_seed = "1\n00:00:01,234 --> 00:00:03,567\nhello\n"
    scenarios = [
        ("srt_vtt_ass_x3", srt_seed, "srt", ("vtt", "ass", "srt"), 3),
        ("ass_identity_x2", ass_seed, "ass", ("ass",), 2),
        ("ass_via_srt_x2", ass_seed, "ass", ("srt", "ass"), 2),
        ("srt_ass_quantization", quant_seed, "srt", ("ass", "srt"), 2),
    ]
    summary = []
    for name, seed, src, chain, cycles in scenarios:
        report = run_chain(seed, src, chain, cycles=cycles)
        write(f"roundtrip_{name}.json",
              json.dumps(roundtrip_error_table(report),
                         ensure_ascii=False, indent=2))
        write(f"roundtrip_{name}.final.{report['final_fmt']}",
              report["final_text"])
        summary.append({
            "scenario": name,
            "chain": list(chain),
            "cycles": cycles,
            "cumulative_drift_ms": report["cumulative_drift_ms"],
            "text_changed_cues": report["text_changed_cues"],
            "per_leg_drift_ms": [leg["leg_drift_ms"]
                                 for leg in report["legs"]],
        })
    return summary


def bilingual_check():
    zh_text = read("samples/bilingual.zh.srt")
    en_text = read("samples/bilingual.en.srt")
    zh0, en0 = convert.parse(zh_text, "srt"), convert.parse(en_text, "srt")
    results = {}
    for chain in (("vtt",), ("vtt", "ass", "srt"), ("ass", "vtt")):
        zt, et = zh_text, en_text
        for dst in chain:
            zt = convert.dump(convert.parse(zt), dst)
            et = convert.dump(convert.parse(et), dst)
        tolerance = 5 if "ass" in chain else 0
        check = verify_bilingual_offsets(zh0, en0, convert.parse(zt),
                                         convert.parse(et), tolerance)
        results["->".join(chain)] = {
            "preserved": check["preserved"],
            "tolerance_ms": tolerance,
            "start_offsets_ms": [o["start_offset_ms"]
                                 for o in check["offset_after"]],
            "end_offsets_ms": [o["end_offset_ms"]
                               for o in check["offset_after"]],
        }
    write("bilingual_offsets.json", json.dumps(results, indent=2))
    return results


def network_check():
    table = compare(loss_rates=(0.0, 0.02, 0.05, 0.10, 0.20),
                    trials=500, seed=20261120)
    slim = [{
        "loss_rate": r["loss_rate"],
        "chunk_first_frame_mean_ms": r["chunk"]["mean_ms"],
        "chunk_first_frame_p95_ms": r["chunk"]["p95_ms"],
        "whole_first_frame_mean_ms": r["whole"]["mean_ms"],
        "whole_first_frame_p95_ms": r["whole"]["p95_ms"],
        "mean_gain_ms": r["mean_gain_ms"],
        "trials_chunk_earlier": r["trials_chunk_earlier"],
        "trials_tied": r["trials_tied"],
        "trials": r["trials"],
    } for r in table]
    write("network_first_frame.json", json.dumps(slim, indent=2))
    lines = ["First-frame arrival at identical loss trace "
             "(500 paired trials)",
             "loss  chunk(mean/p95)   whole(mean/p95)   gain   "
             "chunk-earlier"]
    for r, s in zip(table, slim):
        lines.append(
            f"{r['loss_rate']:4.2f}  "
            f"{r['chunk']['mean_ms']:7.1f}/{r['chunk']['p95_ms']:7.1f}  "
            f"{r['whole']['mean_ms']:7.1f}/{r['whole']['p95_ms']:7.1f}  "
            f"{r['mean_gain_ms']:6.1f}  "
            f"{s['trials_chunk_earlier']}/{r['trials']} "
            f"(tied {s['trials_tied']})")
    write("network_first_frame.txt", "\n".join(lines) + "\n")
    return slim


def main():
    os.makedirs(OUT, exist_ok=True)
    report = {
        "conversion_diffs": conversion_diffs(),
        "loss_inventory": loss_inventory(),
        "roundtrips": roundtrips(),
        "bilingual": bilingual_check(),
        "network": network_check(),
    }
    write("summary.json", json.dumps(report, ensure_ascii=False, indent=2))
    print("reports written to", os.path.relpath(OUT))
    for name in sorted(os.listdir(OUT)):
        print(" -", name)


if __name__ == "__main__":
    main()
