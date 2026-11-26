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


    def test_initialize_migrates_existing_database(self):
        legacy_path = os.path.join(self.temp_dir.name, "legacy.db")
        import sqlite3
        db = sqlite3.connect(legacy_path)
        db.execute("""
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                source TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                market_id TEXT,
                condition_id TEXT,
                token_id TEXT,
                outcome TEXT,
                entry_price REAL,
                reference_prob REAL,
                ev_pct REAL,
                stake_usd REAL,
                details TEXT,
                resolved_at REAL,
                won INTEGER
            )
        """)
        db.commit()
        db.close()
        with patch.object(alert_history, "DB_PATH", legacy_path):
            alert_history.initialize()
            with alert_history._connection() as migrated:
                columns = {row["name"] for row in migrated.execute("PRAGMA table_info(alerts)")}
        self.assertIn("pnl_usd", columns)
        self.assertIn("roi_pct", columns)

    def test_simulated_result_accounts_for_fees_and_binary_outcome(self):
        details = {"gross_shares": 200, "net_shares": 198, "fee_usd": 0.9}
        pnl_win, roi_win = alert_history.simulated_result(
            "EV líquido", 0.5, 100, details, True
        )
        pnl_loss, roi_loss = alert_history.simulated_result(
            "EV líquido", 0.5, 100, details, False
        )
        self.assertAlmostEqual(pnl_win, 98.0)
        self.assertAlmostEqual(roi_win, 98.0)
        self.assertAlmostEqual(pnl_loss, -100.0)
        self.assertAlmostEqual(roi_loss, -100.0)

    def test_non_odds_alerts_do_not_receive_profitability(self):
        pnl, roi = alert_history.simulated_result(
            "BUY", 0.5, 100, {}, True
        )
        self.assertIsNone(pnl)
        self.assertIsNone(roi)

    def test_metrics_separate_simulated_profit_from_win_rate(self):
        market = {"id": "market-3"}
        alert_history.record_alert(
            "odds_value", "EV líquido", market, token_id="token-c",
            entry_price=0.5, stake_usd=100,
            details={"gross_shares": 200, "net_shares": 198},
        )
        with alert_history._connection() as db:
            db.execute(
                "UPDATE alerts SET won = 1, resolved_at = 100, pnl_usd = 98, roi_pct = 98 "
                "WHERE token_id = 'token-c'"
            )
        metrics = alert_history.get_metrics()
        self.assertEqual(metrics["pnl_samples"], 1)
        self.assertAlmostEqual(metrics["simulated_pnl_usd"], 98)
        self.assertAlmostEqual(metrics["avg_roi_pct"], 98)
        self.assertEqual(metrics["calibration"]["samples"], 1)
        self.assertAlmostEqual(metrics["calibration"]["brier_score"], 0.0)
        self.assertEqual(metrics["by_sport"][0]["sport"], "Não identificado")


if __name__ == "__main__":
    unittest.main()
