import unittest

from odds_checker import (
    calcular_probabilidades_sem_vig,
    find_pm_price,
    normalize_team_name,
    teams_match,
)


class OddsMatchingTests(unittest.TestCase):
    def test_normalizes_accents_and_punctuation(self):
        self.assertEqual(normalize_team_name("São Paulo FC!"), "sao paulo fc")

    def test_requires_both_participants_when_event_has_both(self):
        market = {
            "question": "Boston Celtics vs Los Angeles Lakers",
            "outcomes": ["Yes", "No"],
        }
        self.assertTrue(
            teams_match(
                "Boston Celtics", "Los Angeles Lakers",
                market["question"], market,
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

    def test_removes_bookmaker_margin(self):
        fair = calcular_probabilidades_sem_vig([
            {"name": "A", "price": 2.0},
            {"name": "B", "price": 2.0},
        ])
        self.assertAlmostEqual(fair["a"]["fair_prob"], 0.5)
        self.assertAlmostEqual(fair["b"]["fair_prob"], 0.5)


if __name__ == "__main__":
    unittest.main()
