import unittest

from price_tracker import PriceTracker


class PriceTrackerTests(unittest.TestCase):
    def test_detects_price_jump_after_minimum_samples(self):
        tracker = PriceTracker(threshold=0.05, window=60, min_samples=2, alpha=1.0)
        self.assertIsNone(tracker.add_price("asset", 0.50))
        result = tracker.add_price("asset", 0.56)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "SPIKE_UP")
        self.assertAlmostEqual(result[1], 0.50)
        self.assertAlmostEqual(result[2], 0.56)

    def test_rejects_invalid_prices(self):
        tracker = PriceTracker(threshold=0.05, window=60, min_samples=2)
        self.assertIsNone(tracker.add_price("asset", 0))
        self.assertIsNone(tracker.add_price("asset", 1))
        self.assertIsNone(tracker.add_price("asset", float("nan")))


if __name__ == "__main__":
    unittest.main()
