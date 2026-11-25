import unittest
from unittest.mock import AsyncMock, patch

import odds_checker
from odds_checker import (
    calcular_probabilidades_sem_vig,
    check_outcomes,
    find_pm_price,
    normalize_team_name,
    teams_match,
    _infer_yes_side,
    _sport_for_key,
)


class OddsCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        odds_checker._last_odds_alert.clear()

    async def test_failed_delivery_does_not_start_cooldown(self):
        game = {
            "id": "game-1",
            "home_team": "Boston Celtics",
            "away_team": "Los Angeles Lakers",
        }
        pinnacle_market = {
            "outcomes": [
                {"name": "Boston Celtics", "price": 1.50},
                {"name": "Los Angeles Lakers", "price": 3.00},
            ]
        }
        pm_market = {
            "conditionId": "condition-1",
            "question": "Will Boston Celtics win?",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.50", "0.50"],
            "clobTokenIds": ["yes-token", "no-token"],
            "feesEnabled": False,
        }

        fake_ev = {"net_ev_pct": 10.0, "stake_usd": 100, "average_price": 0.5, "best_bid": 0.49, "best_ask": 0.5, "spread_pct": 2, "slippage_pct": 0, "fee_usd": 0, "gross_shares": 200, "net_shares": 200, "net_profit_usd": 10}
        with patch.object(odds_checker, "_estimate_market_ev", new=AsyncMock(side_effect=[fake_ev, None])), patch.object(odds_checker, "send_arbitrage_alert", new=AsyncMock(return_value=False)):
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Boston Celtics", "Los Angeles Lakers", session=object(),
            )

        self.assertEqual(odds_checker._last_odds_alert, {})

    async def test_successful_delivery_starts_cooldown(self):
        game = {
            "id": "game-1",
            "home_team": "Boston Celtics",
            "away_team": "Los Angeles Lakers",
        }
        pinnacle_market = {
            "outcomes": [
                {"name": "Boston Celtics", "price": 1.50},
                {"name": "Los Angeles Lakers", "price": 3.00},
            ]
        }
        pm_market = {
            "conditionId": "condition-1",
            "question": "Will Boston Celtics win?",
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.50", "0.50"],
            "clobTokenIds": ["yes-token", "no-token"],
            "feesEnabled": False,
        }

        fake_ev = {"net_ev_pct": 10.0, "stake_usd": 100, "average_price": 0.5, "best_bid": 0.49, "best_ask": 0.5, "spread_pct": 2, "slippage_pct": 0, "fee_usd": 0, "gross_shares": 200, "net_shares": 200, "net_profit_usd": 10}
        with patch.object(odds_checker, "_estimate_market_ev", new=AsyncMock(side_effect=[fake_ev, None])), patch.object(odds_checker, "send_arbitrage_alert", new=AsyncMock(return_value=True)):
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Boston Celtics", "Los Angeles Lakers", session=object(),
            )

        self.assertEqual(len(odds_checker._last_odds_alert), 1)


class OddsMatchingTests(unittest.TestCase):
    def test_normalizes_accents_and_punctuation(self):
        self.assertEqual(normalize_team_name("São Paulo FC!"), "sao paulo fc")

    def test_matches_both_participants_on_same_event_date(self):
        market = {
            "question": "Boston Celtics vs Los Angeles Lakers",
            "outcomes": ["Yes", "No"],
            "startDate": "2026-11-25T19:00:00Z",
        }
        self.assertTrue(
            teams_match(
                "Boston Celtics", "Los Angeles Lakers",
                market["question"], market, "2026-11-25T23:30:00Z",
            )
        )

    def test_rejects_same_teams_on_a_different_date(self):
        market = {
            "question": "Boston Celtics vs Los Angeles Lakers",
            "outcomes": ["Yes", "No"],
            "startDate": "2026-11-25T19:00:00Z",
        }
        self.assertFalse(
            teams_match(
                "Boston Celtics", "Los Angeles Lakers",
                market["question"], market, "2026-11-26T19:00:00Z",
            )
        )

    def test_rejects_match_when_market_has_no_event_time(self):
        market = {
            "question": "Boston Celtics vs Los Angeles Lakers",
            "outcomes": ["Yes", "No"],
        }
        self.assertFalse(
            teams_match(
                "Boston Celtics", "Los Angeles Lakers",
                market["question"], market, "2026-11-25T19:00:00Z",
            )
        )

    def test_rejects_unrelated_market(self):
        market = {
            "question": "Will Boston Celtics win?",
            "outcomes": ["Yes", "No"],
        }
        self.assertFalse(
            teams_match(
                "Boston Celtics", "Los Angeles Lakers",
                market["question"], market, "2026-11-25T19:00:00Z",
            )
        )

    def test_binary_market_maps_yes_and_no_to_opposite_sides(self):
        market = {
            "outcomes": ["Yes", "No"],
            "outcomePrices": ["0.62", "0.38"],
        }
        self.assertEqual(find_pm_price(market, "home", "home", "Celtics", "Lakers"), 0.62)
        self.assertEqual(find_pm_price(market, "away", "home", "Celtics", "Lakers"), 0.38)

    def test_binary_question_identifies_yes_selection(self):
        market = {"question": "Will Boston Celtics win?", "outcomes": ["Yes", "No"]}
        self.assertEqual(_infer_yes_side(market, "Boston Celtics", "Los Angeles Lakers"), "home")

    def test_discovers_all_requested_sport_families(self):
        samples = [
            ({"key": "basketball_nba", "title": "NBA"}, "NBA"),
            ({"key": "americanfootball_nfl", "title": "NFL"}, "NFL"),
            ({"key": "tennis_atp_french_open", "title": "ATP French Open"}, "Tênis ATP"),
            ({"key": "tennis_wta_french_open", "title": "WTA French Open"}, "Tênis WTA"),
            ({"key": "esports_cs2", "title": "Counter-Strike 2"}, "CS2"),
            ({"key": "esports_valorant", "title": "Valorant"}, "VALORANT"),
        ]
        for payload, expected in samples:
            with self.subTest(expected=expected):
                self.assertEqual(_sport_for_key(payload), expected)

    def test_net_ev_includes_fee_and_walks_ask_depth(self):
        result = odds_checker.calcular_ev_liquido(0.60, {"asks": [{"price": "0.50", "size": "100"}, {"price": "0.55", "size": "100"}], "bids": [{"price": "0.49", "size": "100"}]}, {"rate": 0.05, "exponent": 1}, 75)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["stake_usd"], 75)
        self.assertGreater(result["average_price"], 0.50)
        self.assertGreater(result["fee_usd"], 0)
        self.assertGreater(result["slippage_pct"], 0)

    def test_net_ev_rejects_insufficient_depth(self):
        result = odds_checker.calcular_ev_liquido(0.60, {"asks": [{"price": "0.50", "size": "10"}], "bids": []}, {"rate": 0.05, "exponent": 1}, 100)
        self.assertIsNone(result)

    def test_missing_fee_schedule_is_not_assumed(self):
        self.assertIsNone(odds_checker._fee_schedule({"feesEnabled": True}))
        self.assertEqual(odds_checker._fee_schedule({"feesEnabled": False}), {"rate": 0.0, "exponent": 1.0})
        self.assertEqual(odds_checker._fee_schedule({"feeSchedule": {"rate": 0.05, "exponent": 1}}), {"rate": 0.05, "exponent": 1.0})

    def test_removes_bookmaker_margin(self):
        fair = calcular_probabilidades_sem_vig([
            {"name": "A", "price": 2.0},
            {"name": "B", "price": 2.0},
        ])
        self.assertAlmostEqual(fair["a"]["fair_prob"], 0.5)
        self.assertAlmostEqual(fair["b"]["fair_prob"], 0.5)


if __name__ == "__main__":
    unittest.main()
