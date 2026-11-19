"""Extract ATSC A/53 (GA94) closed-caption data from MPEG-2 user_data.

Scans an MPEG-2 video elementary stream for user_data start codes
(0x000001B2) carrying the GA94 cc_data payload defined by SCTE 128 /
ATSC A/53 Part 4.  Each picture's user_data contributes one frame slot.
"""

from dataclasses import dataclass

from .errors import ErrorLog

USER_DATA_START_CODE = b"\x00\x00\x01\xb2"
GA94_ID = 0x47413934          # 'GA94'
CC_DATA_TYPE = 0x03           # user_data_type_code for cc_data


@dataclass
class CcPair:
    frame: int
    offset: int                # absolute offset of the cc_valid/cc_type byte
    valid: bool
    cc_type: int               # 0=608 fld1, 1=608 fld2, 2=708 pkt data, 3=708 pkt start
    data1: int
    data2: int


def find_user_data(stream: bytes):
    """Yield (payload_offset, payload) for each user_data start code."""
    pos = 0
    while True:
        idx = stream.find(USER_DATA_START_CODE, pos)
        if idx < 0:
            return
        payload_off = idx + 4
        nxt = stream.find(b"\x00\x00\x01", payload_off)
        end = nxt if nxt >= 0 else len(stream)
        yield payload_off, stream[payload_off:end]
        pos = end


def parse_cc_data(payload: bytes, base: int, frame: int, log: ErrorLog):
    """Parse one GA94 cc_data user_data payload into CcPair entries."""
    if len(payload) < 7:
        log.add(base, frame, "mpeg2", "user_data payload too short for GA94 header")
        return []
    ident = int.from_bytes(payload[0:4], "big")
    if ident != GA94_ID:
        log.add(base, frame, "mpeg2",
                f"unknown user_data identifier 0x{ident:08X} (not GA94)")
        return []
    if payload[4] != CC_DATA_TYPE:
        log.add(base + 4, frame, "mpeg2",
                f"unexpected user_data_type_code 0x{payload[4]:02X}")
        return []
    flags = payload[5]
    if not (flags & 0x40):     # process_cc_data_flag
        return []
    cc_count = flags & 0x1F
    body = payload[7:]         # skip em_data byte at [6]
    pairs = []
    for i in range(cc_count):
        off = i * 3
        if off + 3 > len(body):
            log.add(base + 7 + off, frame, "mpeg2",
                    f"cc_data truncated: declared {cc_count} triplets, "
                    f"only {i} present")
            break
        marker_valid = body[off]
        if marker_valid & 0xF8 != 0xF8:
            log.add(base + 7 + off, frame, "mpeg2",
                    f"bad marker bits 0x{marker_valid:02X} in cc triplet {i}")
        pairs.append(CcPair(
            frame=frame,
            offset=base + 7 + off,
            valid=bool(marker_valid & 0x04),
            cc_type=marker_valid & 0x03,
            data1=body[off + 1],
            data2=body[off + 2],
        ))
    return pairs


def extract_cc_pairs(stream: bytes, log: ErrorLog):
    """Return list of CcPair for every picture user_data in the stream."""
    pairs = []
    for frame, (off, payload) in enumerate(find_user_data(stream)):
        pairs.extend(parse_cc_data(payload, off, frame, log))
    return pairs
