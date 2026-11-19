"""E-AC-3 (enhanced AC-3) frame parser, substream splitter and demuxer.

Parses sync frame headers (strmtyp, substreamid, frmsiz, fscod, acmod,
lfeon), validates the frame CRC-16, resynchronizes after corrupt frames,
splits frames into per-substream files and prints per-substream channel /
bitrate statistics plus a cross-substream time-alignment report.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

SYNCWORD = 0x0B77

SAMPLE_RATES = {0: 48000, 1: 44100, 2: 32000}
HALF_SAMPLE_RATES = {0: 24000, 1: 22050, 2: 16000}
NUMBLKS = {0: 1, 1: 2, 2: 3, 3: 6}
ACMOD_CHANNELS = [2, 1, 2, 3, 3, 4, 4, 5]
ACMOD_NAMES = ["1+1", "1/0", "2/0", "3/0", "2/1", "3/1", "2/2", "3/2"]
STRMTYP_NAMES = {0: "independent", 1: "dependent", 2: "associated", 3: "reserved"}


def _build_crc16_table():
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _build_crc16_table()


def crc16(data: bytes) -> int:
    """CRC-16 (poly 0x8005, init 0) as used by AC-3/E-AC-3 frames."""
    crc = 0
    for byte in data:
        crc = ((crc << 8) ^ _CRC16_TABLE[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc


@dataclass
class FrameHeader:
    offset: int
    strmtyp: int
    substreamid: int
    frame_size: int
    sample_rate: int
    num_blocks: int
    acmod: int
    lfeon: int
    bsid: int

    @property
    def channels(self) -> int:
        return ACMOD_CHANNELS[self.acmod] + self.lfeon

    @property
    def channel_name(self) -> str:
        name = ACMOD_NAMES[self.acmod]
        return name + ("+LFE" if self.lfeon else "")

    @property
    def samples(self) -> int:
        return self.num_blocks * 256

    @property
    def duration(self) -> float:
        return self.samples / self.sample_rate

    @property
    def bitrate(self) -> float:
        return self.frame_size * 8 * self.sample_rate / self.samples


def parse_header(buf: bytes, pos: int) -> FrameHeader | None:
    """Parse an E-AC-3 frame header at buf[pos:]; None if not plausible."""
    if pos + 6 > len(buf):
        return None
    if (buf[pos] << 8 | buf[pos + 1]) != SYNCWORD:
        return None
    strmtyp = buf[pos + 2] >> 6
    substreamid = (buf[pos + 2] >> 3) & 0x7
    frmsiz = ((buf[pos + 2] & 0x7) << 8) | buf[pos + 3]
    frame_size = (frmsiz + 1) * 2
    if frame_size < 12:
        return None
    fscod = buf[pos + 4] >> 6
    if fscod == 3:
        fscod2 = (buf[pos + 4] >> 4) & 0x3
        if fscod2 == 3:
            return None
        sample_rate = HALF_SAMPLE_RATES[fscod2]
        numblkscod = 3
    else:
        sample_rate = SAMPLE_RATES[fscod]
        numblkscod = (buf[pos + 4] >> 4) & 0x3
    acmod = (buf[pos + 4] >> 1) & 0x7
    lfeon = buf[pos + 4] & 0x1
    bsid = (buf[pos + 5] >> 3) & 0x1F
    if not 11 <= bsid <= 16:  # E-AC-3 bitstream id range
        return None
    return FrameHeader(
        offset=pos,
        strmtyp=strmtyp,
        substreamid=substreamid,
        frame_size=frame_size,
        sample_rate=sample_rate,
        num_blocks=NUMBLKS[numblkscod],
        acmod=acmod,
        lfeon=lfeon,
        bsid=bsid,
    )


@dataclass
class Frame:
    header: FrameHeader
    data: bytes
    pts_samples: int = 0  # running sample position inside its own substream

    @property
    def pts(self) -> float:
        return self.pts_samples / self.header.sample_rate


@dataclass
class ResyncEvent:
    offset: int
    skipped: int
    reason: str


def iter_frames(buf: bytes):
    """Yield ('frame', Frame) and ('resync', ResyncEvent) while scanning buf.

    A candidate frame is accepted only if the header parses, the declared
    frame size fits in the buffer and the CRC-16 over the whole frame
    (including the trailing crc word) is zero. Otherwise scanning resumes
    one byte later and the gap is reported as a resync event.
    """
    pos = 0
    bad_start = None
    bad_reason = None
    while pos + 6 <= len(buf):
        hdr = parse_header(buf, pos)
        reason = None
        if hdr is None:
            reason = "no valid sync/header"
        elif pos + hdr.frame_size > len(buf):
            reason = f"declared frame size {hdr.frame_size} overruns buffer"
        elif crc16(buf[pos:pos + hdr.frame_size]) != 0:
            reason = f"CRC-16 mismatch (frame size {hdr.frame_size})"
        if reason is None:
            if bad_start is not None:
                yield ("resync", ResyncEvent(bad_start, pos - bad_start, bad_reason))
                bad_start = None
            yield ("frame", Frame(header=hdr, data=buf[pos:pos + hdr.frame_size]))
            pos += hdr.frame_size
        else:
            if bad_start is None:
                bad_start = pos
                bad_reason = reason
            pos += 1
    if bad_start is not None:
        yield ("resync", ResyncEvent(bad_start, len(buf) - bad_start, bad_reason))


@dataclass
class SubstreamStats:
    substreamid: int
    strmtyp: int = -1
    sample_rate: int = 0
    frames: list = field(default_factory=list)
    channel_configs: set = field(default_factory=set)
    _samples_acc: int = 0

    def add(self, frame: Frame, assign_pts: bool = True) -> None:
        hdr = frame.header
        if not self.frames:
            self.strmtyp = hdr.strmtyp
            self.sample_rate = hdr.sample_rate
        if assign_pts:
            frame.pts_samples = self._samples_acc
        self._samples_acc += hdr.samples
        self.frames.append(frame)
        self.channel_configs.add((hdr.acmod, hdr.lfeon))

    @property
    def total_samples(self) -> int:
        return self._samples_acc

    @property
    def duration(self) -> float:
        return self.total_samples / self.sample_rate if self.sample_rate else 0.0

    @property
    def avg_bitrate(self) -> float:
        total_bytes = sum(f.header.frame_size for f in self.frames)
        return total_bytes * 8 / self.duration if self.duration else 0.0

    def channel_desc(self) -> str:
        parts = []
        for acmod, lfeon in sorted(self.channel_configs):
            ch = ACMOD_CHANNELS[acmod] + lfeon
            parts.append(f"{ch}ch ({ACMOD_NAMES[acmod]}{'+LFE' if lfeon else ''})")
        return ", ".join(parts)


def report_substreams(substreams: dict) -> None:
    print("== Substream summary ==")
    for sid in sorted(substreams):
        st = substreams[sid]
        print(
            f"substream {sid} [{STRMTYP_NAMES.get(st.strmtyp, '?')}]: "
            f"{len(st.frames)} frames, {st.sample_rate} Hz, "
            f"channels: {st.channel_desc()}, "
            f"avg bitrate: {st.avg_bitrate / 1000:.1f} kbps, "
            f"duration: {st.duration:.3f} s"
        )


def report_program_config(ordered: list, substreams: dict, slots: int = 12) -> None:
    """Show how the program channel count evolves (e.g. 7.1 <-> 5.1)."""
    indep = next((s for s in substreams.values() if s.strmtyp == 0), None)
    dep = next((s for s in substreams.values() if s.strmtyp == 1), None)
    if indep is None or dep is None:
        return
    dep_channels = dep.frames[0].header.channels
    print("== Program channel layout over time (independent + dependent) ==")
    slot = 0
    for i, frame in enumerate(ordered):
        if frame.header.strmtyp != 0 or slot >= slots:
            continue
        has_dep = (i + 1 < len(ordered)
                   and ordered[i + 1].header.strmtyp == 1)
        total = frame.header.channels + (dep_channels if has_dep else 0)
        layout = f"{total - 1}.1" if frame.header.lfeon else f"{total}.0"
        print(
            f"  slot {slot:3d}  pts={frame.pts:8.4f}s  "
            f"independent={frame.header.channels}ch "
            f"dependent={'+' + str(dep_channels) + 'ch' if has_dep else 'absent'} "
            f"-> program {layout}"
        )
        slot += 1


def report_alignment(substreams: dict, max_rows: int = 16) -> None:
    """Merged timeline of all substreams, sorted by presentation time."""
    events = []
    for sid, st in substreams.items():
        for idx, frame in enumerate(st.frames):
            events.append((frame.pts, sid, idx, frame))
    events.sort(key=lambda e: (e[0], e[1]))
    print(f"== Time alignment across substreams (first {max_rows} events) ==")
    print(f"  {'pts(s)':>10}  {'substream':>9}  {'frame#':>6}  {'pts_samples':>11}  {'rate':>6}")
    for pts, sid, idx, frame in events[:max_rows]:
        print(
            f"  {pts:10.5f}  {sid:>9}  {idx:>6}  "
            f"{frame.pts_samples:>11}  {frame.header.sample_rate:>6}"
        )


def cross_check(substreams: dict) -> bool:
    """Verify per-substream PTS independence and sample-rate conversion."""
    ok = True
    print("== Cross-substream timestamp checks ==")
    for sid in sorted(substreams):
        st = substreams[sid]
        mono = all(
            st.frames[i + 1].pts > st.frames[i].pts
            for i in range(len(st.frames) - 1)
        )
        print(
            f"  substream {sid}: {len(st.frames)} frames, PTS strictly "
            f"increasing: {'OK' if mono else 'FAIL'}"
        )
        ok &= mono
    main = next((s for s in substreams.values() if s.strmtyp == 0), None)
    assoc = next((s for s in substreams.values() if s.strmtyp == 2), None)
    if main and assoc and main.sample_rate != assoc.sample_rate:
        ratio = assoc.sample_rate / main.sample_rate
        print(
            f"  sample-rate conversion: main {main.sample_rate} Hz vs "
            f"associated {assoc.sample_rate} Hz (ratio {ratio:.6f})"
        )
        # Show the conversion of a few associated-stream timestamps into
        # main-rate sample units.
        for idx in (0, len(assoc.frames) // 2, -1):
            f = assoc.frames[idx]
            converted = f.pts_samples / ratio
            print(
                f"    assoc frame {idx % len(assoc.frames):3d}: "
                f"{f.pts_samples} samples @ {assoc.sample_rate} Hz "
                f"-> {converted:9.1f} samples @ {main.sample_rate} Hz "
                f"= {f.pts:.6f} s"
            )
        # End-of-stream positions must land within one frame period.
        main_end = main.frames[-1].pts + main.frames[-1].header.duration
        assoc_end = assoc.frames[-1].pts + assoc.frames[-1].header.duration
        drift_ms = abs(main_end - assoc_end) * 1000
        print(
            f"    main stream ends at {main_end:.6f} s, "
            f"associated stream ends at {assoc_end:.6f} s"
        )
        print(f"    end-of-stream divergence: {drift_ms:.3f} ms")
        conv_ok = drift_ms < max(main.frames[0].header.duration,
                                 assoc.frames[0].header.duration) * 1000
        print(f"    conversion within one frame period: {'OK' if conv_ok else 'FAIL'}")
        ok &= conv_ok
    return ok


def demux(substreams: dict, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    print(f"== Demuxing to {outdir}/ ==")
    for sid in sorted(substreams):
        st = substreams[sid]
        name = STRMTYP_NAMES.get(st.strmtyp, "unknown")
        path = os.path.join(outdir, f"substream{sid}_{name}_{st.sample_rate}Hz.eac3")
        with open(path, "wb") as fh:
            for frame in st.frames:
                fh.write(frame.data)
        print(f"  wrote {path} ({len(st.frames)} frames)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="E-AC-3 elementary stream file")
    ap.add_argument("--demux", metavar="DIR", help="write per-substream files to DIR")
    ap.add_argument("--align-rows", type=int, default=16)
    args = ap.parse_args(argv)

    with open(args.input, "rb") as fh:
        buf = fh.read()
    print(f"input: {args.input} ({len(buf)} bytes)")

    substreams: dict[int, SubstreamStats] = {}
    resyncs: list[ResyncEvent] = []
    ordered: list[Frame] = []
    for kind, item in iter_frames(buf):
        if kind == "frame":
            hdr = item.header
            sid = hdr.substreamid
            st = substreams.setdefault(sid, SubstreamStats(sid))
            if hdr.strmtyp == 1 and 0 in substreams and substreams[0].frames:
                # Dependent substream frames belong to the current slot of
                # the independent substream: inherit its timeline.
                indep = substreams[0]
                item.pts_samples = round(
                    indep.frames[-1].pts_samples
                    * hdr.sample_rate / indep.sample_rate
                )
                st.add(item, assign_pts=False)
            else:
                st.add(item)
            ordered.append(item)
        else:
            resyncs.append(item)

    if resyncs:
        print("== Resync events (bad frames skipped) ==")
        for ev in resyncs:
            print(f"  offset {ev.offset}: skipped {ev.skipped} bytes ({ev.reason})")
    else:
        print("== No resync needed: stream is clean ==")

    report_substreams(substreams)
    report_program_config(ordered, substreams)
    report_alignment(substreams, args.align_rows)
    ok = cross_check(substreams)
    if args.demux:
        demux(substreams, args.demux)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
