"""End-to-end CEA-608/708 extraction, overlay analysis and RTSP recovery.

Usage: python3 main.py [sample.m2v]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cc_pipeline.errors import ErrorLog
from cc_pipeline.mpeg2 import extract_cc_pairs
from cc_pipeline.cea608 import decode_608
from cc_pipeline.cea708 import decode_708
from cc_pipeline.overlay import (compose, event_region, frames_overlap,
                                 role_of, source_rank)
from cc_pipeline.rtsp_sim import simulate_outage

FPS = 30000 / 1001          # NTSC 29.97


def timecode(frame):
    secs = frame / FPS
    mm, ss = divmod(secs, 60)
    hh, mm = divmod(int(mm), 60)
    return f"{hh:02d}:{mm:02d}:{ss:06.3f}"


def fmt_end(frame):
    return "open" if frame is None else str(frame)


def report(stream_path):
    with open(stream_path, "rb") as fh:
        data = fh.read()
    log = ErrorLog()
    pairs = extract_cc_pairs(data, log)
    ev608, st608 = decode_608(pairs, log)
    ev708, st708 = decode_708(pairs, log)
    events = sorted(ev608 + ev708,
                    key=lambda e: (e.start_frame, source_rank(e.source)))

    print("=" * 72)
    print(f"1. 字幕事件清单 (共 {len(events)} 条, 帧率 {FPS:.3f} fps)")
    print("=" * 72)
    for ev in events:
        if ev.source.startswith("708"):
            loc = f"708窗口区域(75x210网格)={ev.region}"
        else:
            loc = f"行={list(ev.rows)}"
        print(f"[{ev.source}] ({role_of(ev.source)}) "
              f"帧 {ev.start_frame}..{fmt_end(ev.end_frame)} "
              f"({timecode(ev.start_frame)}.."
              f"{timecode(ev.end_frame or ev.start_frame)}) {loc}")
        print(f"    文本: {ev.text!r}")

    print()
    print("=" * 72)
    print("2. 样式切换记录")
    print("=" * 72)
    for frame, src, desc in sorted(st608 + st708):
        print(f"帧 {frame:>4} ({timecode(frame)}) [{src}] -> {desc}")

    print()
    print("=" * 72)
    print("3. 双通道优先级与叠加分析")
    print("=" * 72)
    z_order, conflicts, relocations, suppressed = compose(events)
    print("优先级: 708-S1 > 608-CC1 > 608-CC2 > 708-S2 > 608-CC3 > 608-CC4")
    print("叠加顺序 (z-order, 先画底层 -> 后画顶层):")
    for ev in z_order:
        print(f"    [{ev.source}] 帧 {ev.start_frame}.."
              f"{fmt_end(ev.end_frame)} {ev.text[:30]!r}")
    for note in suppressed:
        print(f"抑制: {note}")
    if conflicts:
        print("!! 主/副字幕区域冲突:")
        for main, sub in conflicts:
            print(f"    冲突: [{main.source}] 与 [{sub.source}] 在帧 "
                  f"{max(main.start_frame, sub.start_frame)} 附近重叠")
        for fix in relocations:
            print(f"    建议: {fix}")
    else:
        print("确认: 主字幕(底部区域)与副字幕(顶部区域)在所有并发帧上"
              "区域不相交, 不会互相覆盖。")
        mains = [e for e in events if role_of(e.source) == "main"]
        subs = [e for e in events if role_of(e.source) == "sub"]
        for m in mains:
            for s in subs:
                if frames_overlap(m, s):
                    mr, sr = event_region(m), event_region(s)
                    lo = max(m.start_frame, s.start_frame)
                    hi = min(m.end_frame or 0, s.end_frame or 0)
                    print(f"    帧 {lo}..{hi}: "
                          f"主{m.source} 行{mr[0]:.0f}%-{mr[1]:.0f}% | "
                          f"副{s.source} 行{sr[0]:.0f}%-{sr[1]:.0f}% "
                          "-> 无交叠")

    print()
    print("=" * 72)
    print(f"4. 控制码解析错误字节位置 (共 {len(log)} 处)")
    print("=" * 72)
    for line in log.report_lines():
        print(f"    {line}")

    print()
    print("=" * 72)
    print("5. RTSP 网络中断重连模拟")
    print("=" * 72)
    checks, trace = simulate_outage()
    for line in trace:
        print(f"    {line}")
    all_ok = True
    for name, ok in checks:
        all_ok &= ok
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print()
    print("结论:", "全部检查通过" if all_ok and not conflicts
          else "存在失败项, 见上文")
    return 0 if all_ok and not conflicts else 1


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "samples", "sample.m2v")
    sys.exit(report(path))
