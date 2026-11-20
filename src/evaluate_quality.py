#!/usr/bin/env python3
import json
import re
import statistics
import tempfile
from pathlib import Path

from media_utils import find_tool, probe_frames, run, write_csv, write_json, ROOT

FPS = 24
PEAK_BPS = 800_000
MODES = {
    "cbr": {
        "label": "CBR",
        "settings": "b=min=max=800k, bufsize=800k, nal-hrd=cbr",
    },
    "abr": {
        "label": "ABR",
        "settings": "b=500k, maxrate=800k, bufsize=800k",
    },
    "vbv": {
        "label": "VBV/CRF",
        "settings": "crf=12, maxrate=800k, bufsize=800k",
    },
}
REGIONS = {
    "static_a": (0.0, 3.0),
    "motion": (3.0, 9.0),
    "static_b": (9.0, 12.0),
}


def packet_rows(path):
    ffprobe = find_tool("ffprobe")
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,size,flags",
        "-of", "csv=p=0", str(path),
    ]
    rows = []
    for line in run(cmd).stdout.splitlines():
        fields = line.split(",")
        if len(fields) < 3 or not fields[0] or not fields[1]:
            continue
        rows.append({
            "pts": float(fields[0]),
            "size": int(fields[1]),
            "key": "K" in fields[2],
        })
    return sorted(rows, key=lambda row: row["pts"])


def parse_psnr(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        match = re.search(r"\bn:(\d+)\b", line)
        value = re.search(r"\bpsnr_avg:([0-9.]+|inf)\b", line)
        if match and value:
            values[int(match.group(1))] = (
                float("inf") if value.group(1) == "inf" else float(value.group(1))
            )
    return values


def parse_ssim(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        match = re.search(r"\bn:(\d+)\b", line)
        value = re.search(r"\bAll:([0-9.]+)\b", line)
        if match and value:
            values[int(match.group(1))] = float(value.group(1))
    return values


def quality_metrics(ffmpeg, encoded, source, tmp):
    psnr_file = tmp / f"{encoded.stem}.psnr"
    ssim_file = tmp / f"{encoded.stem}.ssim"
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(encoded), "-i", str(source),
        "-filter_complex", f"[0:v][1:v]psnr=stats_file={psnr_file}",
        "-f", "null", "-",
    ])
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-i", str(encoded), "-i", str(source),
        "-filter_complex", f"[0:v][1:v]ssim=stats_file={ssim_file}",
        "-f", "null", "-",
    ])
    return parse_psnr(psnr_file), parse_ssim(ssim_file)


def one_second_peak(packets):
    times = [row["pts"] for row in packets]
    bits = [row["size"] * 8 for row in packets]
    peak = 0
    left = 0
    window = 0
    for right, timestamp in enumerate(times):
        window += bits[right]
        while timestamp - times[left] >= 1.0:
            window -= bits[left]
            left += 1
        peak = max(peak, window)
    return peak


def region_summary(rows, low, high):
    selected = [
        row for row in rows
        if low <= row["time_s"] < high and row["psnr_avg"] != float("inf")
    ]
    finite_ssim = [row["ssim_all"] for row in selected]
    return {
        "frames": len(selected),
        "psnr_avg_db": statistics.fmean(row["psnr_avg"] for row in selected),
        "ssim_avg": statistics.fmean(finite_ssim),
        "ssim_min": min(finite_ssim),
    }


def main():
    ffmpeg = find_tool("ffmpeg")
    source = ROOT / "artifacts/media/source.mp4"
    all_rows = []
    summaries = {}
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        for mode in MODES:
            encoded = ROOT / f"artifacts/media/{mode}.mp4"
            packets = packet_rows(encoded)
            psnr, ssim = quality_metrics(ffmpeg, encoded, source, tmp)
            frames = probe_frames(encoded)
            if not (len(packets) == len(psnr) == len(ssim) == len(frames)):
                raise RuntimeError(f"metric/frame count mismatch for {mode}")
            rows = []
            for index, (packet, frame) in enumerate(zip(packets, frames), start=1):
                rows.append({
                    "mode": mode,
                    "frame": index - 1,
                    "time_s": round((index - 1) / FPS, 6),
                    "keyframe": "yes" if frame["key"] else "no",
                    "bytes": packet["size"],
                    "psnr_avg": psnr[index],
                    "ssim_all": ssim[index],
                })
            all_rows.extend(rows)
            total_bits = sum(row["bytes"] * 8 for row in rows)
            duration = len(rows) / FPS
            summary = {
                "label": MODES[mode]["label"],
                "settings": MODES[mode]["settings"],
                "configured_maxrate_bps": PEAK_BPS,
                "avg_bitrate_bps": total_bits / duration,
                "observed_peak_1s_bps": one_second_peak(packets),
                "psnr_avg_db": statistics.fmean(row["psnr_avg"] for row in rows),
                "ssim_avg": statistics.fmean(row["ssim_all"] for row in rows),
                "regions": {},
            }
            for name, (low, high) in REGIONS.items():
                summary["regions"][name] = region_summary(rows, low, high)
            summaries[mode] = summary

    for row in all_rows:
        if row["psnr_avg"] == float("inf"):
            row["psnr_avg"] = "inf"
        row["psnr_avg"] = (
            f'{row["psnr_avg"]:.6f}'
            if isinstance(row["psnr_avg"], float) else row["psnr_avg"]
        )
        row["ssim_all"] = f'{row["ssim_all"]:.9f}'
        row["time_s"] = f'{row["time_s"]:.6f}'

    ssim_order = sorted(summaries, key=lambda m: summaries[m]["ssim_avg"], reverse=True)
    psnr_order = sorted(summaries, key=lambda m: summaries[m]["psnr_avg_db"], reverse=True)
    vbv_static = statistics.fmean([
        summaries["vbv"]["regions"]["static_a"]["ssim_avg"],
        summaries["vbv"]["regions"]["static_b"]["ssim_avg"],
    ])
    best_static = max(
        statistics.fmean([
            summaries[mode]["regions"]["static_a"]["ssim_avg"],
            summaries[mode]["regions"]["static_b"]["ssim_avg"],
        ]) for mode in summaries
    )
    checks = {
        "same_configured_peak": all(
            item["configured_maxrate_bps"] == PEAK_BPS for item in summaries.values()
        ),
        "vbv_static_ssim_above_0_999": vbv_static >= 0.999,
        "vbv_static_within_0_001_of_best": best_static - vbv_static <= 0.001,
        "vbv_static_better_than_vbv_motion": (
            vbv_static > summaries["vbv"]["regions"]["motion"]["ssim_avg"]
        ),
    }
    output = {
        "modes": MODES,
        "summary": summaries,
        "ranking": {
            "by_ssim": ssim_order,
            "by_psnr": psnr_order,
        },
        "vbv_static_mean_ssim": vbv_static,
        "best_static_mean_ssim": best_static,
        "vbv_static_gap_to_best": best_static - vbv_static,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
    }
    write_csv(
        ROOT / "artifacts/results/quality_per_frame.csv",
        list(all_rows[0].keys()), all_rows,
    )
    write_csv(
        ROOT / "artifacts/results/quality.csv",
        ["mode", "label", "settings", "avg_bitrate_bps", "observed_peak_1s_bps",
         "psnr_avg_db", "ssim_avg"],
        [{
            "mode": mode,
            "label": item["label"],
            "settings": item["settings"],
            "avg_bitrate_bps": f'{item["avg_bitrate_bps"]:.1f}',
            "observed_peak_1s_bps": item["observed_peak_1s_bps"],
            "psnr_avg_db": f'{item["psnr_avg_db"]:.6f}',
            "ssim_avg": f'{item["ssim_avg"]:.9f}',
        } for mode, item in summaries.items()],
    )
    write_json(ROOT / "artifacts/results/quality_summary.json", output)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
