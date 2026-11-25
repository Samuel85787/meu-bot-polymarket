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
        }

        with patch.object(odds_checker, "send_arbitrage_alert", new=AsyncMock(return_value=False)):
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Boston Celtics", "Los Angeles Lakers",
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
        }

        with patch.object(odds_checker, "send_arbitrage_alert", new=AsyncMock(return_value=True)):
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Boston Celtics", "Los Angeles Lakers",
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

    def test_removes_bookmaker_margin(self):
        fair = calcular_probabilidades_sem_vig([
            {"name": "A", "price": 2.0},
            {"name": "B", "price": 2.0},
        ])
        self.assertAlmostEqual(fair["a"]["fair_prob"], 0.5)
        self.assertAlmostEqual(fair["b"]["fair_prob"], 0.5)


if __name__ == "__main__":
    unittest.main()
