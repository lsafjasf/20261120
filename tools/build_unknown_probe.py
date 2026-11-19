"""Build a stream containing reserved/unspecified NAL types for preservation tests."""

from __future__ import annotations

import argparse
from pathlib import Path


def unknown_nal(nal_type: int, tag: bytes) -> bytes:
    header = bytes((0x00, (nal_type << 3) | 1))
    payload = tag + b"\x00\x00\x03" + tag[::-1]
    return b"\x00\x00\x01" + header + payload


def insert_at(data: bytes, offset: int, addition: bytes) -> bytes:
    if offset < 0 or offset > len(data):
        raise ValueError("insertion offset is outside the stream")
    if offset < len(data) and not (
        data[offset : offset + 3] == b"\x00\x00\x01"
        or data[offset : offset + 4] == b"\x00\x00\x00\x01"
    ):
        raise ValueError(f"offset {offset} is not a NAL boundary")
    return data[:offset] + addition + data[offset:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--low-size", type=int, required=True)
    parser.add_argument("--high-size", type=int, required=True)
    args = parser.parse_args()

    data = args.base.read_bytes()
    high_start = args.low_size
    low_again_start = args.low_size + args.high_size
    additions = [
        (0, unknown_nal(26, b"RSV-NVCL-26")),
        (high_start, unknown_nal(27, b"RSV-NVCL-27")),
        (low_again_start, unknown_nal(29, b"UNSPEC-29")),
        (len(data), unknown_nal(31, b"UNSPEC-31")),
    ]
    for offset, addition in reversed(additions):
        data = insert_at(data, offset, addition)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)


if __name__ == "__main__":
    main()
