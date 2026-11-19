#!/usr/bin/env python3
"""Synthesize a TrueHD sample whose first major sync header uses extensions.

The encoder only emits 28-byte major sync headers, so we splice extension
words into the first access unit, set the extension flag, recompute the
major-sync CRC16 and fix the access-unit length word. All other bytes stay
byte-identical, which lets us prove core parameters still parse correctly.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
from truehd_parser import SYNC_WORD, mlp_crc16  # noqa: E402


def build(src, dst, extension_words=2):
    data = bytearray(open(src, "rb").read())
    sync = data.find(SYNC_WORD)
    au_start = sync - 4
    length_word = struct.unpack_from(">H", data, au_start)[0]
    declared = (length_word & 0x0FFF) * 2
    parity_nibble = (length_word >> 12) & 0xF

    # 28-byte header: extension flag is buf[25] bit0; checksum at buf[26..27].
    checksum_pos = sync + 26
    data[sync + 25] |= 0x01

    # Extension block = count nibble byte (+1 reserved byte) then ext words.
    added = 2 + extension_words * 2
    ext_block = bytearray(added)
    ext_block[0] = extension_words << 4  # high nibble of the new buf[26]
    new_data = data[:checksum_pos] + ext_block + data[checksum_pos:]

    new_header_size = 28 + added
    # FFmpeg calls ff_mlp_checksum16(buf, header_size - 2): its inner CRC
    # covers (header_size - 2) - 2 = header_size - 4 bytes, then XORs the
    # LE checksum at buf + header_size - 2.
    crc_input_len = new_header_size - 4
    crc = mlp_crc16(bytes(new_data[sync:sync + crc_input_len]))
    struct.pack_into("<H", new_data, sync + new_header_size - 2, crc)

    # Fix the access-unit length word (grows by `added`).
    new_length = declared + added
    # Recompute the parity nibble exactly like the encoder's
    # write_frame_headers: XOR timing, length word and every substream header,
    # fold to a nibble, then store nibble ^ 0xF in the AU header high bits.
    sync_pos = au_start + 4
    hdr_size = 28 + added
    sub_hdr_block = au_start + 4 + hdr_size
    length_word = new_length // 2
    timing_word = struct.unpack_from(">H", new_data, au_start + 2)[0]
    parity = timing_word ^ length_word
    for idx in range(2):  # two 2-byte substream headers
        parity ^= struct.unpack_from(">H", new_data,
                                     sub_hdr_block + idx * 2)[0]
    parity ^= parity >> 8
    parity ^= parity >> 4
    parity &= 0xF
    new_word = ((parity ^ 0xF) << 12) | length_word
    struct.pack_into(">H", new_data, au_start, new_word)
    open(dst, "wb").write(new_data)
    print("wrote", dst, len(new_data), "bytes, header_size", new_header_size,
          "crc", hex(crc))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 2)
