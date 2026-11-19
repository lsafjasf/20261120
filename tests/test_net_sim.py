import unittest

from net_sim import simulate, compare
from net_sim.sim import ChunkConfig


class NetSimTests(unittest.TestCase):
    def test_zero_loss_chunk_earlier(self):
        result = simulate(0.0, trials=50)
        self.assertTrue(result["chunk_always_earlier_or_tied"])
        self.assertLess(result["chunk"]["mean_ms"],
                        result["whole"]["mean_ms"])
        self.assertEqual(result["trials_chunk_earlier"], 50)

    def test_packet_counts(self):
        result = simulate(0.0, trials=4, object_bytes=60_000,
                          mtu_bytes=1_500, chunk_bytes=8_000)
        self.assertEqual(result["packets_first_chunk"], 6)
        self.assertEqual(result["packets_whole_object"], 40)

    def test_reproducible_seed(self):
        first = simulate(0.1, trials=20, seed=7)
        second = simulate(0.1, trials=20, seed=7)
        self.assertEqual([r["gain_ms"] for r in first["rows"]],
                         [r["gain_ms"] for r in second["rows"]])

    def test_higher_loss_increases_arrival_time(self):
        table = compare(loss_rates=(0.0, 0.05, 0.2), trials=200)
        self.assertLess(table[0]["chunk"]["mean_ms"],
                        table[2]["chunk"]["mean_ms"])
        for row in table:
            self.assertTrue(row["chunk_always_earlier_or_tied"])

    def test_config_send_time(self):
        cfg = ChunkConfig(mtu_bytes=1_500, send_rate_mbps=5.0)
        # 1500 bytes * 8 / 5 Mbps = 2.4 ms
        self.assertAlmostEqual(cfg.packet_send_ms, 2.4)


if __name__ == "__main__":
    unittest.main()
