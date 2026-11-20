#!/usr/bin/env python3
import csv
import json
import os
import statistics
import struct
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_tool(name):
    env = os.environ.get(name.upper())
    candidates = [
        env,
        f"/tmp/ffmpeg-7.0.2-amd64-static/{name}",
        str(ROOT / ".tools" / "ffmpeg" / name),
    ]
    lookup = subprocess.run(
        ["bash", "-lc", f"command -v {name} || true"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    candidates.append(lookup)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            probe = subprocess.run(
                [candidate, "-version"], capture_output=True, check=False
            )
            if probe.returncode == 0:
                return candidate
    raise RuntimeError(f"{name} not found; set {name.upper()}=/path/to/{name}")


def run(cmd, check=True):
    return subprocess.run(
        [str(x) for x in cmd], capture_output=True, text=True, check=check
    )


def ffprobe_json(path, entries):
    ffprobe = find_tool("ffprobe")
    cmd = [ffprobe, "-v", "error", "-of", "json"]
    for entry in entries:
        cmd += ["-show_entries", entry]
    cmd.append(str(path))
    return json.loads(run(cmd).stdout)


def probe_frames(path):
    ffprobe = find_tool("ffprobe")
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "frame=key_frame,pts_time",
        "-of", "csv=p=0", str(path),
    ]
    rows = []
    for line in run(cmd).stdout.splitlines():
        fields = line.split(",")
        if len(fields) < 2 or not fields[0]:
            continue
        rows.append({
            "key": int(fields[0]) == 1,
            "pts": float(fields[1]),
        })
    return rows


class Box:
    def __init__(self, typ, start, size, header):
        self.typ = typ
        self.start = start
        self.size = size
        self.header = header

    @property
    def payload(self):
        return self.start + self.header

    @property
    def end(self):
        return self.start + self.size


def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, raw_type = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        yield Box(raw_type.decode("latin1"), pos, size, header)
        pos += size


CONTAINERS = {
    "moov", "trak", "mdia", "minf", "stbl", "moof", "traf", "mvex", "dinf"
}


def find_box(data, path, start=0, end=None):
    wanted = path[0] if isinstance(path, (list, tuple)) else path
    rest = list(path[1:]) if isinstance(path, (list, tuple)) else []
    for box in boxes(data, start, end):
        if box.typ != wanted:
            continue
        if not rest:
            return box
        if box.typ in CONTAINERS:
            found = find_box(data, rest, box.payload, box.end)
            if found:
                return found
    return None


def parse_init(data):
    ftyp = find_box(data, "ftyp")
    moov = find_box(data, "moov")
    if not ftyp or not moov:
        raise ValueError("init lacks ftyp/moov")
    mdhd = find_box(data, ["moov", "trak", "mdia", "mdhd"])
    if not mdhd:
        raise ValueError("init lacks mdhd")
    version = data[mdhd.payload]
    if version == 1:
        timescale = struct.unpack_from(">I", data, mdhd.payload + 20)[0]
        duration = struct.unpack_from(">Q", data, mdhd.payload + 24)[0]
    else:
        timescale = struct.unpack_from(">I", data, mdhd.payload + 12)[0]
        duration = struct.unpack_from(">I", data, mdhd.payload + 16)[0]
    stsd = find_box(data, ["moov", "trak", "mdia", "minf", "stbl", "stsd"])
    avcc = find_box(data, "avcC", stsd.payload, stsd.end)
    nal_length_size = (data[avcc.payload + 4] & 3) + 1 if avcc else 4
    return {
        "init_bytes": data[ftyp.start:ftyp.end] + data[moov.start:moov.end],
        "timescale": timescale,
        "duration": duration / timescale,
        "nal_length_size": nal_length_size,
    }


def parse_tfdt(data, fragment):
    tfdt = find_box(data, ["moof", "traf", "tfdt"], fragment.start, fragment.end)
    if not tfdt:
        raise ValueError("fragment lacks tfdt")
    version = data[tfdt.payload]
    offset = tfdt.payload + 4
    if version == 1:
        return struct.unpack_from(">Q", data, offset)[0]
    return struct.unpack_from(">I", data, offset)[0]


def parse_nal_types(data, fragment, mdat, length_size=4):
    nal_types = []
    pos = mdat.payload
    while pos + length_size <= mdat.end:
        size = int.from_bytes(data[pos:pos + length_size], "big")
        pos += length_size
        if size <= 0 or pos + size > mdat.end:
            break
        nal_types.append(data[pos] & 0x1F)
        pos += size
    return nal_types


def decode_first_frame_seconds(path, ffmpeg, runs=3):
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        run([ffmpeg, "-v", "error", "-i", str(path),
             "-frames:v", "1", "-f", "null", "-"])
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def write_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
