#!/usr/bin/env python3
"""Cross-validate the hand-written TrueHD parser against FFmpeg."""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from analyze_truehd import analyze  # noqa: E402


FFMPEG = os.environ.get("FFMPEG", "/tmp/ffmpeg-7.0.2-amd64-static/ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "/tmp/ffmpeg-7.0.2-amd64-static/ffprobe")


def ffprobe_json(path):
    cmd = [
        FFPROBE, "-hide_banner", "-loglevel", "error",
        "-show_streams", "-show_packets", "-of", "json", path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def remux(path, dest):
    cmd = [FFMPEG, "-hide_banner", "-y", "-i", path,
           "-c", "copy", "-f", "truehd", dest]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stderr


def decode_null(path):
    cmd = [FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", path,
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stderr.strip().splitlines()


def main(paths):
    reports = []
    for path in paths:
        parser = analyze(path)
        probe = ffprobe_json(path)
        stream = probe["streams"][0]
        packets = probe["packets"]
        ext_offsets = [int(p["pos"]) for p in packets]
        parser_offsets = parser["frames"]["first_offsets"]

        with tempfile.TemporaryDirectory() as tmp:
            remuxed = os.path.join(tmp, "remux.thd")
            remux_rc, remux_log = remux(path, remuxed)
            remux_bytes = os.path.getsize(remuxed) if os.path.exists(remuxed) else 0
            decode_rc, decode_errors = decode_null(path)

        complete_bytes = sum(
            f["declared_bytes"] for f in [
                {"declared_bytes": parser["frames"]["first_offsets"][-1]}
            ]
        ) if False else None
        # Parser's complete-frame byte span:
        indexed = parser["frames"]["total_access_units_indexed"]
        complete = parser["frames"]["complete_access_units"]
        if indexed == complete:
            parser_complete_bytes = parser["file_size_bytes"]
        else:
            tail = parser["truncated_tail_frames"][0]
            parser_complete_bytes = tail["offset"]

        boundary_match = ext_offsets[:len(parser_offsets)] == parser_offsets
        report = {
            "file": os.path.basename(path),
            "ffprobe": {
                "codec_name": stream.get("codec_name"),
                "sample_rate": int(stream.get("sample_rate", 0)),
                "channels": stream.get("channels"),
                "channel_layout": stream.get("channel_layout"),
                "bits_per_raw_sample": stream.get("bits_per_raw_sample"),
            },
            "parser": {
                "sample_rate_hz": parser["major_sync"]["sample_rate_hz"],
                "declared_channels":
                    parser["channels"]["declared"]["declared_channel_count"],
                "num_substreams": parser["major_sync"]["num_substreams"],
                "access_unit_frames": indexed,
                "complete_frames": complete,
                "major_sync_checksum_ok":
                    parser["major_sync"]["checksum_ok"],
                "all_parity_checks_ok":
                    parser["frames"]["all_parity_checks_ok"],
            },
            "sample_rate_match":
                int(stream.get("sample_rate", 0)) ==
                parser["major_sync"]["sample_rate_hz"],
            "channel_count_match":
                int(stream.get("channels", 0)) ==
                parser["channels"]["declared"]["declared_channel_count"],
            "external_packet_count": len(packets),
            "external_packet_offsets_first_8": ext_offsets[:8],
            "parser_frame_offsets_first_8": parser_offsets,
            "block_boundary_offsets_match": boundary_match,
            "packet_count_match": len(packets) == complete,
            "external_bytes_written": remux_bytes,
            "parser_complete_frame_bytes": parser_complete_bytes,
            "remux_byte_identical_on_complete_prefix":
                remux_bytes == parser_complete_bytes,
            "remux_returncode": remux_rc,
            "decoder_returncode": decode_rc,
            "decoder_errors": decode_errors,
        }
        reports.append(report)
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
