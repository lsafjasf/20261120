"""Generate a synthetic MPEG-2 video ES with GA94 closed-caption user data.

Layout: sequence header -> GOP header -> [picture + user_data] * N

Embeds:
  * 608 field 1 / CC1: pop-on caption with a mid-row italics switch
  * 608 field 2 / CC3: roll-up caption (two lines, top rows)
  * 708 service 1:     bottom-anchored window, SPA style switch
  * deliberately corrupted bytes at recorded offsets
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def odd_parity(b):
    return b | (0x80 if bin(b & 0x7F).count("1") % 2 == 0 else 0)


def cc608(b1, b2):
    return (odd_parity(b1), odd_parity(b2))


def triplet(cc_type, d1, d2, valid=True, marker=0xF8):
    return bytes([marker | (0x04 if valid else 0) | cc_type, d1, d2])


def user_data_payload(triplets):
    body = b"".join(triplets)
    header = b"GA94" + bytes([0x03, 0x40 | len(triplets), 0xFF])
    return b"\x00\x00\x01\xb2" + header + body + b"\xff"


def dtvcc_packets(block, start_frame):
    """Split a service-1 DTVCC packet into (frame, triplet) entries."""
    pkt = bytes([(1 << 5) | len(block)]) + block
    if not len(pkt) % 2:       # DTVCC packets are odd-length; pad inside
        pkt += b"\x00"
    size_code = (len(pkt) + 1) // 2
    out = [(start_frame, triplet(3, size_code, pkt[0]))]
    frame = start_frame
    idx = 1
    while idx < len(pkt):
        frame += 1
        out.append((frame, triplet(2, pkt[idx], pkt[idx + 1])))
        idx += 2
    return out


def build():
    frames = {}

    def put(frame, *trips):
        frames.setdefault(frame, []).extend(trips)

    # --- 608 CC1 pop-on: "MAIN TITLE" with mid-row italics
    put(0, triplet(0, *cc608(0x14, 0x20)))          # RCL
    put(1, triplet(0, *cc608(0x14, 0x20)))          # RCL repeat (ignored)
    put(2, triplet(0, *cc608(0x14, 0x50)))          # PAC row 15 white
    for i, ch in enumerate(b"MAIN "):
        put(3 + i, triplet(0, *cc608(ch, 0x00)))
    put(8, triplet(0, *cc608(0x11, 0x2E)))          # mid-row: italics
    for i, ch in enumerate(b"TITLE"):
        put(9 + i, triplet(0, *cc608(ch, 0x00)))
    put(14, triplet(0, *cc608(0x14, 0x2F)))         # EOC -> display
    put(60, triplet(0, *cc608(0x14, 0x2C)))         # EDM -> erase

    # --- 608 CC3 roll-up: two lines on top rows
    put(12, triplet(1, *cc608(0x14, 0x25)))         # RU2 (field 2)
    put(13, triplet(1, *cc608(0x10, 0x60)))         # PAC row 1
    for i, ch in enumerate(b"sub one"):
        put(14 + i, triplet(1, *cc608(ch, 0x00)))
    put(22, triplet(1, *cc608(0x14, 0x2D)))         # CR
    put(23, triplet(1, *cc608(0x11, 0x60)))         # PAC row 2 white
    for i, ch in enumerate(b"sub two"):
        put(24 + i, triplet(1, *cc608(ch, 0x00)))
    put(40, triplet(1, *cc608(0x14, 0x2C)))         # EDM

    # --- 708 service 1: bottom window with SPA italics
    df0 = bytes([0x80, 0x98, 0x3F, 0x70, 60, 100, 0x21, 0x10,
                 0x89, 0x01]) + b"708 PRIMARY"
    for frame, tri in dtvcc_packets(df0, 20):
        put(frame, tri)
    spa = bytes([0x90, 0xC0, 0x00]) + b" MORE"
    for frame, tri in dtvcc_packets(spa, 33):
        put(frame, tri)
    hdw = bytes([0x8A, 0x01])
    for frame, tri in dtvcc_packets(hdw, 55):
        put(frame, tri)

    return frames


def corruptions():
    """Return {frame: [(triplet, err_byte_in_triplet, description)]}."""
    bad = {}

    def put(frame, tri, err_off, desc):
        bad.setdefault(frame, []).append((tri, err_off, desc))

    # 1. parity-corrupted 608 text byte ('A' with even parity)
    put(45, triplet(0, 0x41, odd_parity(0x00)), 1, "parity error")
    # 2. reserved 608 control code 0x14 0x22 (valid parity)
    put(46, triplet(0, *cc608(0x14, 0x22)), 1, "reserved control code")
    # 3. cc triplet with broken marker bits
    put(47, triplet(0, *cc608(0x48, 0x49), marker=0xF0), 0,
        "bad marker bits")
    # 4. DTVCC packet start (size 19) abandoned after 2 bytes, then a
    #    new packet start -> truncation error at the new start
    put(48, triplet(3, 10, 0x21), None, None)
    put(49, triplet(2, 0x41, 0x42), None, None)
    # 5. replacement packet carrying reserved C1 code 0x93
    pkt = bytes([(1 << 5) | 1, 0x93])
    if not len(pkt) % 2:
        pkt += b"\x00"
    put(50, triplet(3, (len(pkt) + 1) // 2, pkt[0]), 0,
        "new packet start before previous packet completed")
    put(51, triplet(2, pkt[1], pkt[2]), 1, "reserved C1 code 0x93")
    # 6. stray packet-data pair with no open packet
    put(52, triplet(2, 0x43, 0x44), 0, "packet data without packet start")
    return bad


def build_stream():
    frames = build()
    bad = corruptions()
    for frame, items in bad.items():
        frames.setdefault(frame, []).extend(t for t, _, _ in items)

    stream = bytearray()
    stream += b"\x00\x00\x01\xb3" + bytes(12)      # sequence header
    stream += b"\x00\x00\x01\xb8" + bytes(4)       # GOP header
    expected = []
    for frame in range(max(frames) + 1):
        stream += b"\x00\x00\x01\x00" + bytes([0x10, frame & 0xFF])
        trips = frames.get(frame, [])
        payload = user_data_payload(trips)
        base = len(stream) + 4 + 7                 # start code + GA94 header
        err_lookup = {}
        for tri, err_off, desc in bad.get(frame, []):
            if err_off is not None:
                err_lookup[id(tri)] = (err_off, desc)
        for i, tri in enumerate(trips):
            if id(tri) in err_lookup:
                err_off, desc = err_lookup[id(tri)]
                expected.append((base + i * 3 + err_off, desc))
        stream += payload
    return bytes(stream), sorted(expected)


if __name__ == "__main__":
    data, expected = build_stream()
    out = os.path.join(os.path.dirname(__file__), "sample.m2v")
    with open(out, "wb") as fh:
        fh.write(data)
    print(f"wrote {out} ({len(data)} bytes)")
    for off, desc in expected:
        print(f"  expected error @ 0x{off:08X}: {desc}")
