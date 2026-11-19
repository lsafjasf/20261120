import json
import unittest

from mp2inspect import MP2Scanner, scan

from .helpers import SAMPLES, read_sample


class SideInfoTests(unittest.TestCase):
    def test_crc_and_side_info_layout(self):
        frame = scan(read_sample("stereo_44100_protected.mp2")).frames[0]
        side = frame.side_info
        self.assertIsNotNone(side)
        assert side is not None
        self.assertEqual(side.allocation_table, 0)
        self.assertEqual(side.sblimit, 27)
        self.assertEqual(side.bound, 27)
        self.assertEqual(side.channels, 2)
        self.assertEqual(len(side.allocation), 2)
        self.assertEqual(len(side.allocation[0]), 27)
        self.assertEqual(side.protected_bits, 216)
        self.assertEqual(side.side_info_start_bit, 48)
        self.assertEqual(side.protected_end_bit, 264)
        self.assertTrue(frame.crc_ok)
        self.assertEqual(frame.header.bitrate_kbps, 128)

    def test_free_format_side_info_uses_inferred_table(self):
        frame = scan(read_sample("stereo_44100_free.mp2")).frames[0]
        side = frame.side_info
        self.assertIsNotNone(side)
        assert side is not None
        self.assertEqual(side.allocation_table, 0)
        self.assertEqual(side.sblimit, 27)
        self.assertIsNone(side.crc_stored)
        self.assertIsNone(side.crc_calculated)

    def test_channel_allocation_order_is_preserved(self):
        result = scan(read_sample("channel_order_packets.mp2"))
        expected_left = (
            (1, 0, 0),
            (0, 0, 3),
            (0, 0, 0, 0, 0, 4),
            (0, 0, 0, 0, 0, 0, 0, 6),
        )
        expected_right = (
            (0, 2, 0),
            (0, 0, 0, 0, 1, 0),
            (0, 0, 0, 0, 0, 0, 5),
            (0, 0, 0, 0, 0, 0, 0, 0, 7),
        )
        self.assertEqual(result.total_frames, 4)
        self.assertTrue(all(frame.crc_ok for frame in result.frames))
        for frame, left, right in zip(
            result.frames, expected_left, expected_right, strict=True
        ):
            side = frame.side_info
            self.assertIsNotNone(side)
            assert side is not None
            self.assertEqual(side.allocation[0][: len(left)], left)
            self.assertEqual(side.allocation[1][: len(right)], right)
        self.assertEqual(result.frames[0].side_info.allocation[0][0], 1)
        self.assertEqual(result.frames[0].side_info.allocation[1][1], 2)
        self.assertEqual(result.frames[3].side_info.allocation[0][7], 6)
        self.assertEqual(result.frames[3].side_info.allocation[1][8], 7)


class PacketBoundaryTests(unittest.TestCase):
    def test_irregular_packets_preserve_channel_order(self):
        data = read_sample("channel_order_packets.mp2")
        manifest = json.loads((SAMPLES / "fixture_manifest.json").read_text())
        lengths = manifest["channel_order_packets"]["packet_lengths"]
        self.assertEqual(sum(lengths), len(data))

        scanner = MP2Scanner()
        frames = []
        cursor = 0
        for length in lengths:
            frames.extend(scanner.feed(data[cursor : cursor + length]))
            cursor += length
        frames.extend(scanner.finish())

        contiguous = scan(data).frames
        self.assertEqual(len(frames), 4)
        self.assertEqual(
            [frame.to_dict() for frame in frames],
            [frame.to_dict() for frame in contiguous],
        )
        self.assertEqual(
            [frame.side_info.allocation[0][:3] for frame in frames],
            [(1, 0, 0), (0, 0, 3), (0, 0, 0), (0, 0, 0)],
        )

    def test_every_byte_as_a_packet(self):
        data = read_sample("channel_order_packets.mp2")
        scanner = MP2Scanner()
        frames = []
        for value in data:
            frames.extend(scanner.feed(bytes((value,))))
        frames.extend(scanner.finish())
        self.assertEqual(len(frames), 4)
        self.assertEqual(
            [frame.offset for frame in frames],
            [0, 417, 835, 1252],
        )
        self.assertEqual(frames[0].side_info.allocation[0][0], 1)
        self.assertEqual(frames[0].side_info.allocation[1][1], 2)

    def test_free_format_across_irregular_packets(self):
        data = read_sample("stereo_44100_free.mp2")
        scanner = MP2Scanner()
        frames = []
        cursor = 0
        lengths = (1, 7, 3, 29, 4, 53, 2, 31)
        packet = 0
        while cursor < len(data):
            length = lengths[packet % len(lengths)]
            frames.extend(scanner.feed(data[cursor : cursor + length]))
            cursor += length
            packet += 1
        frames.extend(scanner.finish())
        self.assertEqual(len(frames), 77)
        self.assertTrue(all(frame.free_format for frame in frames))
        self.assertEqual([frame.offset for frame in frames], [i * 417 for i in range(77)])


if __name__ == "__main__":
    unittest.main()
