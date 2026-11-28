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

    def test_replay_backtest_scales_historical_alert_returns(self):
        market = {"id": "market-replay", "_sport": "NBA"}
        alert_history.record_alert(
            "odds_value", "EV líquido", market, token_id="replay-win",
            entry_price=0.5, reference_prob=0.6, stake_usd=100,
            ev_pct=10, details={"gross_shares": 200, "net_shares": 198},
        )
        alert_history.record_alert(
            "odds_value", "EV líquido", market, token_id="replay-loss",
            entry_price=0.5, reference_prob=0.6, stake_usd=100,
            ev_pct=10, details={"gross_shares": 200, "net_shares": 198},
        )
        with alert_history._connection() as db:
            db.execute("UPDATE alerts SET won = 1, resolved_at = 100 WHERE token_id = 'replay-win'")
            db.execute("UPDATE alerts SET won = 0, resolved_at = 100 WHERE token_id = 'replay-loss'")
        result = alert_history.replay_backtest(50, min_ev_pct=5, sport="NBA")
        self.assertEqual(result["samples"], 2)
        self.assertEqual(result["wins"], 1)
        self.assertAlmostEqual(result["total_pnl_usd"], -1.0)
        self.assertEqual(result["by_sport"]["NBA"]["samples"], 2)

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
            entry_price=0.5, reference_prob=0.60, stake_usd=100,
            details={"gross_shares": 200, "net_shares": 198},
        )
        with alert_history._connection() as db:
            db.execute(
                "UPDATE alerts SET won = 1, resolved_at = 100, pnl_usd = 98, roi_pct = 98 "
                "WHERE token_id = 'token-c'"
            )
        metrics = alert_history.get_metrics()
        self.assertEqual(metrics["pnl_samples"], 1)
        self.assertEqual(metrics["odds_settled"], 1)
        self.assertEqual(metrics["odds_wins"], 1)
        self.assertAlmostEqual(metrics["odds_win_rate"], 100.0)
        self.assertAlmostEqual(metrics["simulated_pnl_usd"], 98)
        self.assertAlmostEqual(metrics["avg_roi_pct"], 98)
        self.assertEqual(metrics["calibration"]["samples"], 1)
        self.assertAlmostEqual(metrics["calibration"]["brier_score"], 0.16)
        self.assertEqual(metrics["by_sport"][0]["sport"], "Não identificado")


    def test_recent_summary_and_sport_ranking_use_resolved_simulated_results(self):
        market = {"id": "period-nba", "_sport": "NBA"}
        for index, won, pnl, roi in [
            (1, 1, 98.0, 98.0),
            (2, 1, 48.0, 48.0),
            (3, 0, -100.0, -100.0),
        ]:
            token = f"nba-{index}"
            alert_history.record_alert(
                "odds_value", "EV líquido", market, token_id=token,
                entry_price=0.5, reference_prob=0.6, ev_pct=8,
                stake_usd=100,
            )
            with alert_history._connection() as db:
                db.execute(
                    "UPDATE alerts SET won = ?, resolved_at = ?, pnl_usd = ?, roi_pct = ? "
                    "WHERE token_id = ?",
                    (won, 200, pnl, roi, token),
                )

        alert_history.record_alert("whale", "BUY", market)
        alert_history.record_alert("price_spike", "SPIKE_UP", market)

        summary = alert_history.get_period_metrics(24)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["whale_alerts"], 1)
        self.assertEqual(summary["spike_alerts"], 1)
        self.assertEqual(summary["odds_alerts"], 3)
        self.assertEqual(summary["odds_wins"], 2)
        self.assertEqual(summary["odds_losses"], 1)
        self.assertEqual(summary["pnl_samples"], 3)
        self.assertAlmostEqual(summary["simulated_pnl_usd"], 46.0)

        ranking = alert_history.get_sport_ranking(30, min_samples=3)
        self.assertEqual(len(ranking), 1)
        self.assertEqual(ranking[0]["sport"], "NBA")
        self.assertEqual(ranking[0]["samples"], 3)
        self.assertAlmostEqual(ranking[0]["win_rate"], 200 / 3)

    def test_sport_ranking_excludes_small_samples(self):
        market = {"id": "few-samples", "_sport": "NFL"}
        for index in range(2):
            token = f"nfl-{index}"
            alert_history.record_alert(
                "odds_value", "EV líquido", market, token_id=token,
                entry_price=0.5, reference_prob=0.6, ev_pct=8, stake_usd=100,
            )
            with alert_history._connection() as db:
                db.execute(
                    "UPDATE alerts SET won = 1, resolved_at = 200, pnl_usd = 10, roi_pct = 10 "
                    "WHERE token_id = ?", (token,)
                )
        self.assertEqual(alert_history.get_sport_ranking(30, min_samples=3), [])


if __name__ == "__main__":
    unittest.main()
