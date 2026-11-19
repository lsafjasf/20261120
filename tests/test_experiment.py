from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from mp4_stream_lab.experiment import CHUNK_SIZE, _integrity_check
from mp4_stream_lab.media import generate_main_stream, generate_tile_streams
from mp4_stream_lab.mp4 import duplicate_keys, parse_samples, sample_digest
from mp4_stream_lab.network import NetworkConfig, simulate_transfer
from mp4_stream_lab.tiles import analyse_tiles


class GeneratedMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.main = cls.root / "source.mp4"
        generate_main_stream(cls.main)
        cls.manifest = cls.root / "tiles/manifest.json"
        generate_tile_streams(cls.main, cls.root / "tiles")
        cls.data = cls.main.read_bytes()
        cls.samples = parse_samples(cls.data)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_parser_matches_ffprobe_packet_ranges(self) -> None:
        output = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "packet=pos,size",
                "-of",
                "json",
                str(self.main),
            ]
        )
        packets = json.loads(output)["packets"]
        self.assertEqual(len(packets), len(self.samples))
        for packet, sample in zip(packets, self.samples):
            self.assertEqual(int(packet["pos"]), sample.offset)
            self.assertEqual(int(packet["size"]), sample.size)

    def test_chunk_reassembly_preserves_and_does_not_duplicate_samples(self) -> None:
        integrity, chunks = _integrity_check(self.data, CHUNK_SIZE)
        self.assertTrue(integrity["passed"])
        self.assertGreater(integrity["samples_spanning_chunk_boundaries"], 0)
        self.assertEqual(integrity["original_sha256"], integrity["reassembled_sha256"])
        self.assertEqual(integrity["duplicate_sample_keys"], [])
        self.assertEqual(b"".join(chunks), self.data)

    def test_no_loss_chunk_gap_has_no_extra_rtt(self) -> None:
        config = NetworkConfig(loss_percent=0, packet_payload_bytes=1024)
        transfer = simulate_transfer(self.data, CHUNK_SIZE, config, chunked=True)
        gaps = [chunk["gap_before_ms"] for chunk in transfer.chunks[1:]]
        bound = (
            config.chunk_header_bytes
            + config.packet_header_bytes
            + config.packet_payload_bytes
        ) / config.bytes_per_second * 1000.0
        self.assertGreater(len(gaps), 0)
        self.assertTrue(all(gap > 0 for gap in gaps))
        self.assertLessEqual(max(gaps), bound + 0.01)
        self.assertLess(max(gaps), config.rtt_ms)
        self.assertEqual(CHUNK_SIZE % config.packet_payload_bytes, 0)

    def test_loss_simulation_is_deterministic(self) -> None:
        config = NetworkConfig(
            loss_percent=10, loss_seed=77, packet_payload_bytes=1024
        )
        first = simulate_transfer(self.data, CHUNK_SIZE, config, chunked=True)
        second = simulate_transfer(self.data, CHUNK_SIZE, config, chunked=True)
        whole = simulate_transfer(self.data, len(self.data), config, chunked=False)
        self.assertGreater(first.lost_initial_packets, 0)
        self.assertEqual(first.lost_initial_packets, second.lost_initial_packets)
        self.assertEqual(first.retransmissions, second.retransmissions)
        self.assertEqual(first.packet_count, whole.packet_count)
        self.assertEqual(first.lost_initial_packets, whole.lost_initial_packets)

    def test_tile_statistics_and_seams(self) -> None:
        result = analyse_tiles(
            self.main,
            self.manifest,
            self.root / "tile-report.json",
            self.root / "tile-comparison.png",
        )
        self.assertTrue(result["geometry_aligned"])
        self.assertTrue(result["edge_and_bytes_follow_same_rank"])
        self.assertTrue(result["seams_aligned"])
        self.assertGreaterEqual(result["mean_mosaic_psnr_db"], 30.0)
        self.assertEqual(len(result["tiles"]), 4)

    def test_sample_payloads_are_unique_entries(self) -> None:
        self.assertEqual(duplicate_keys(self.samples), [])
        self.assertEqual(
            len([sample_digest(self.data, sample) for sample in self.samples]),
            len(self.samples),
        )


if __name__ == "__main__":
    unittest.main()
