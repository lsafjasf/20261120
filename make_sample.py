"""Generate a synthetic E-AC-3 sample stream for parser verification.

Layout (40 slots):
  - substream 0: independent, 48 kHz, 5.1 (acmod 7 + LFE), always present
  - substream 1: dependent,   48 kHz, 2.0, present on even slots only
                 -> program alternates 7.1 (even slots) / 5.1 (odd slots)
  - substream 2: associated,  44.1 kHz, 1.0 commentary, muxed on the common
                 timeline (its frame cadence differs from the 48 kHz main)

Deliberate corruptions to exercise resync:
  - slot 10: 100 garbage bytes inserted before the slot
  - slot 20: main frame declares a wrong (too large) frmsiz
  - associated frame #27 has a flipped payload byte (CRC failure)
"""
import random

from eac3 import crc16

SLOTS = 40
ASSOC_CORRUPT_INDEX = 27


def build_frame(strmtyp, substreamid, frame_size, fscod, numblkscod,
                acmod, lfeon, seed):
    frmsiz = frame_size // 2 - 1
    hdr = bytearray(6)
    hdr[0] = 0x0B
    hdr[1] = 0x77
    hdr[2] = (strmtyp << 6) | (substreamid << 3) | (frmsiz >> 8)
    hdr[3] = frmsiz & 0xFF
    hdr[4] = (fscod << 6) | (numblkscod << 4) | (acmod << 1) | lfeon
    hdr[5] = 16 << 3  # bsid = 16 (E-AC-3)
    rng = random.Random(seed)
    body = bytes(rng.randrange(256) for _ in range(frame_size - 6 - 2))
    frame = bytes(hdr) + body
    crc = crc16(frame)
    return frame + crc.to_bytes(2, "big")


def main_frame(seed):
    # 48 kHz, 6 blocks, 3/2+LFE (5.1), 1792 bytes -> 448 kbps
    return build_frame(0, 0, 1792, fscod=0, numblkscod=3, acmod=7, lfeon=1,
                       seed=seed)


def dependent_frame(seed):
    # 48 kHz, 6 blocks, 2/0 (2.0), 768 bytes -> 192 kbps
    return build_frame(1, 1, 768, fscod=0, numblkscod=3, acmod=2, lfeon=0,
                       seed=seed)


def associated_frame(seed):
    # 44.1 kHz, 6 blocks, 1/0 (1.0), 418 bytes -> ~96.3 kbps
    return build_frame(2, 2, 418, fscod=1, numblkscod=3, acmod=1, lfeon=0,
                       seed=seed)


def corrupt_frmsiz(frame):
    """Declare a larger frmsiz than the real frame length."""
    frame = bytearray(frame)
    frmsiz = ((frame[2] & 0x7) << 8) | frame[3]
    frmsiz += 64  # +128 bytes
    frame[2] = (frame[2] & 0xF8) | (frmsiz >> 8)
    frame[3] = frmsiz & 0xFF
    return bytes(frame)


def flip_payload_byte(frame):
    frame = bytearray(frame)
    frame[len(frame) // 2] ^= 0xFF
    return bytes(frame)


def main():
    out = bytearray()
    rng = random.Random(1234)
    main_pts = 0    # 48 kHz sample units
    assoc_pts = 0   # 44.1 kHz sample units
    assoc_count = 0
    for slot in range(SLOTS):
        if slot == 10:
            out += bytes(rng.randrange(256) for _ in range(100))
        main = main_frame(seed=1000 + slot)
        if slot == 20:
            main = corrupt_frmsiz(main)
        out += main
        if slot % 2 == 0:
            out += dependent_frame(seed=2000 + slot)
        # Mux associated frames on the common timeline: emit while the
        # associated stream's clock has not passed the main stream's.
        while assoc_pts / 44100 <= main_pts / 48000:
            assoc = associated_frame(seed=3000 + assoc_count)
            if assoc_count == ASSOC_CORRUPT_INDEX:
                assoc = flip_payload_byte(assoc)
            out += assoc
            assoc_pts += 1536
            assoc_count += 1
        main_pts += 1536
    with open("sample.eac3", "wb") as fh:
        fh.write(out)
    print(f"wrote sample.eac3 ({len(out)} bytes, {SLOTS} slots, "
          f"{assoc_count} associated frames)")


if __name__ == "__main__":
    main()
