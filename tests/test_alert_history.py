import os
import tempfile
import unittest
from unittest.mock import patch

import alert_history


class AlertHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "alerts.db")
        self.path_patch = patch.object(alert_history, "DB_PATH", self.db_path)
        self.path_patch.start()
        alert_history.initialize()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_records_alert_and_counts_pending_outcome(self):
        market = {"id": "market-1", "conditionId": "condition-1"}
        self.assertTrue(alert_history.record_alert(
            "odds_value", "EV líquido", market, token_id="token-yes",
            outcome="Team A", entry_price=0.45, reference_prob=0.60,
            ev_pct=12.5, stake_usd=100,
        ))
        metrics = alert_history.get_metrics()
        self.assertEqual(metrics["total"], 1)
        self.assertEqual(metrics["pending"], 1)
        self.assertEqual(metrics["settled"], 0)
        self.assertAlmostEqual(metrics["avg_ev"], 12.5)

    def test_counts_resolved_wins_and_losses(self):
        market = {"id": "market-2"}
        alert_history.record_alert("price_spike", "SPIKE_UP", market, token_id="token-a")
        alert_history.record_alert("whale", "BUY", market, token_id="token-b")
        with alert_history._connection() as db:
            db.execute("UPDATE alerts SET won = 1, resolved_at = 100 WHERE token_id = 'token-a'")
            db.execute("UPDATE alerts SET won = 0, resolved_at = 100 WHERE token_id = 'token-b'")
        metrics = alert_history.get_metrics()
        self.assertEqual(metrics["settled"], 2)
        self.assertEqual(metrics["wins"], 1)
        self.assertEqual(metrics["losses"], 1)
        self.assertEqual(metrics["win_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()
