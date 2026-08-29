import unittest

from ce_mcp.performance_smoke import percentile


class PerformanceSmokeTests(unittest.TestCase):
    def test_nearest_rank_percentile_is_deterministic(self) -> None:
        values = [float(value) for value in range(1, 101)]
        self.assertEqual(percentile(values, 0.50), 50.0)
        self.assertEqual(percentile(values, 0.95), 95.0)


if __name__ == "__main__":
    unittest.main()
