import unittest
from datetime import datetime, timedelta, timezone
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
    detectar_superficie_tenis,
    selecionar_feeds_para_ciclo,
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

        fake_ev = {"net_ev_pct": 10.0, "stake_usd": 100, "average_price": 0.5, "best_bid": 0.49, "best_ask": 0.5, "spread_pct": 2, "slippage_pct": 0, "fee_usd": 0, "gross_shares": 200, "net_shares": 200, "net_profit_usd": 10, "top_ask_depth_usd": 100, "book_age_seconds": 1}
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

        fake_ev = {"net_ev_pct": 10.0, "stake_usd": 100, "average_price": 0.5, "best_bid": 0.49, "best_ask": 0.5, "spread_pct": 2, "slippage_pct": 0, "fee_usd": 0, "gross_shares": 200, "net_shares": 200, "net_profit_usd": 10, "top_ask_depth_usd": 100, "book_age_seconds": 1}
        with patch.object(odds_checker, "_estimate_market_ev", new=AsyncMock(side_effect=[fake_ev, None])), patch.object(odds_checker, "send_arbitrage_alert", new=AsyncMock(return_value=True)):
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Boston Celtics", "Los Angeles Lakers", session=object(),
            )

        self.assertEqual(len(odds_checker._last_odds_alert), 1)




class TennisAutomaticAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        odds_checker._last_odds_alert.clear()

    async def test_surface_detector_uses_explicit_surface_metadata(self):
        self.assertEqual(
            detectar_superficie_tenis({"surface": "Clay"}, {}),
            "clay",
        )
        self.assertEqual(
            detectar_superficie_tenis({}, {"description": "ATP match on grass court"}),
            "grass",
        )
        self.assertIsNone(
            detectar_superficie_tenis({}, {"question": "Will Sinner win easily?"})
        )

    async def test_qualified_prematch_tennis_alert_includes_aces_analysis(self):
        inicio = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        game = {
            "id": "tennis-match-1",
            "home_team": "Jannik Sinner",
            "away_team": "Alexander Zverev",
            "commence_time": inicio,
        }
        pinnacle_market = {
            "outcomes": [
                {"name": "Jannik Sinner", "price": 1.50},
                {"name": "Alexander Zverev", "price": 3.00},
            ]
        }
        pm_market = {
            "_sport": "Tênis ATP",
            "conditionId": "tennis-condition-1",
            "question": "Will Jannik Sinner beat Alexander Zverev?",
            "surface": "hard",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": ["yes-token", "no-token"],
            "feesEnabled": False,
        }
        fake_ev = {
            "net_ev_pct": 10.0, "stake_usd": 100, "average_price": 0.5,
            "best_bid": 0.49, "best_ask": 0.5, "spread_pct": 2,
            "slippage_pct": 0, "fee_usd": 0, "gross_shares": 200,
            "net_shares": 200, "net_profit_usd": 10,
            "top_ask_depth_usd": 100, "book_age_seconds": 1,
        }
        analise = {
            "status": "ok", "reference_prob": None, "selected_player": None,
            "surface": "hard", "league": "tennis",
        }
        with patch.object(odds_checker, "_estimate_market_ev",
                          new=AsyncMock(side_effect=[fake_ev, None])), \
             patch.object(odds_checker, "analisar_matchup",
                          new=AsyncMock(return_value=analise)) as analisar, \
             patch.object(odds_checker, "send_arbitrage_alert",
                          new=AsyncMock(return_value=True)) as send:
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Jannik Sinner", "Alexander Zverev", session=object(),
            )

        analisar.assert_awaited_once_with(
            "Jannik Sinner", "Alexander Zverev",
            league="tennis", surface="hard",
        )
        self.assertTrue(send.await_args.kwargs["tennis_analysis"]["status"] == "ok")

    async def test_live_or_unknown_start_time_does_not_spend_aces_credits(self):
        self.assertFalse(odds_checker._eh_pre_jogo({"commence_time": None}))
        passado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertFalse(odds_checker._eh_pre_jogo({"commence_time": passado}))


    async def test_qualified_prematch_nba_alert_includes_current_espn_context(self):
        inicio = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        game = {
            "id": "nba-match-1",
            "home_team": "Dallas Mavericks",
            "away_team": "Golden State Warriors",
            "commence_time": inicio,
        }
        pinnacle_market = {
            "outcomes": [
                {"name": "Dallas Mavericks", "price": 1.75},
                {"name": "Golden State Warriors", "price": 2.20},
            ]
        }
        pm_market = {
            "_sport": "NBA",
            "conditionId": "nba-condition-1",
            "question": "Dallas Mavericks vs Golden State Warriors",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": ["yes-token", "no-token"],
            "feesEnabled": False,
        }
        fake_ev = {
            "net_ev_pct": 9.0, "stake_usd": 50, "average_price": 0.5,
            "best_bid": 0.49, "best_ask": 0.5, "spread_pct": 2,
            "slippage_pct": 0, "fee_usd": 0, "gross_shares": 100,
            "net_shares": 100, "net_profit_usd": 4.5,
            "top_ask_depth_usd": 100, "book_age_seconds": 1,
        }
        analysis = {
            "status": "ok", "sport": "NBA", "season": "2026-27",
            "home": {"name": "Dallas Mavericks", "record": {}},
            "away": {"name": "Golden State Warriors", "record": {}},
            "h2h": {"wins_home": 1, "wins_away": 0, "total": 1, "recent": []},
            "updated_at": "17:00",
        }
        with patch.object(odds_checker, "_estimate_market_ev",
                          new=AsyncMock(side_effect=[fake_ev, None])), \
             patch.object(odds_checker, "analisar_confronto",
                          new=AsyncMock(return_value=analysis)) as analisar, \
             patch.object(odds_checker, "send_arbitrage_alert",
                          new=AsyncMock(return_value=True)) as send:
            await check_outcomes(
                game, pinnacle_market, pm_market,
                "Dallas Mavericks", "Golden State Warriors", session=object(),
            )

        analisar.assert_awaited_once_with(
            "NBA", "Dallas Mavericks", "Golden State Warriors"
        )
        self.assertEqual(send.await_args.kwargs["sports_analysis"]["season"], "2026-27")


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

    def test_quality_filter_rejects_wide_spread_slippage_and_thin_top_level(self):
        base = {"spread_pct": 2, "slippage_pct": 1, "top_ask_depth_usd": 50, "book_age_seconds": 1}
        self.assertEqual(odds_checker.avaliar_qualidade_livro(base, 100), (True, "ok"))
        self.assertFalse(odds_checker.avaliar_qualidade_livro(
            {**base, "spread_pct": 9}, 100
        )[0])
        self.assertFalse(odds_checker.avaliar_qualidade_livro(
            {**base, "slippage_pct": 4}, 100
        )[0])
        self.assertFalse(odds_checker.avaliar_qualidade_livro(
            {**base, "top_ask_depth_usd": 10}, 100
        )[0])
        self.assertFalse(odds_checker.avaliar_qualidade_livro(
            {"spread_pct": 1}, 100
        )[0])
        self.assertFalse(odds_checker.avaliar_qualidade_livro(
            {**base, "book_age_seconds": None}, 100
        )[0])
        self.assertFalse(odds_checker.avaliar_qualidade_livro(
            {**base, "book_age_seconds": 45}, 100
        )[0])
        self.assertIsNone(odds_checker._book_age_seconds("not-a-timestamp"))

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


class OddsLiveContextTests(unittest.TestCase):
    def test_live_score_uses_only_available_score_feed_data(self):
        live = {"score": "20-14", "period": "Q2", "elapsed": "04:12", "live": True}
        with patch.object(odds_checker, "obter_placar_mercado", return_value=live):
            self.assertEqual(
                odds_checker.formatar_placar_evento({"gameId": "game-1"}),
                "AO VIVO | 20-14 | Q2 | 04:12",
            )
        finalizado = {"score": "101-99", "period": "Final", "ended": True}
        with patch.object(odds_checker, "obter_placar_mercado", return_value=finalizado):
            self.assertEqual(
                odds_checker.formatar_placar_evento({"gameId": "game-2"}),
                "ENCERRADO | 101-99 | Final",
            )
        with patch.object(odds_checker, "obter_placar_mercado", return_value=None):
            self.assertIsNone(odds_checker.formatar_placar_evento({"gameId": "game-3"}))
class OddsQuotaTests(unittest.TestCase):
    def tearDown(self):
        odds_checker._quota_used = None
        odds_checker._quota_remaining = None
        odds_checker._quota_last = None
        odds_checker._quota_checked_at = None
        odds_checker._sport_rotation.clear()

    def test_quota_status_uses_headers_and_preserves_reserve(self):
        with patch.object(odds_checker.config, "ODDS_API_CREDITS_RESERVE", 50):
            odds_checker.atualizar_quota({
                "x-requests-used": "450",
                "x-requests-remaining": "50",
                "x-requests-last": "1",
            })
            status = odds_checker.get_quota_status()
        self.assertEqual(status["used"], 450)
        self.assertEqual(status["remaining"], 50)
        self.assertEqual(status["total"], 500)
        self.assertTrue(status["paused"])

    def test_quota_allows_queries_above_reserve(self):
        with patch.object(odds_checker.config, "ODDS_API_CREDITS_RESERVE", 50):
            odds_checker.atualizar_quota({
                "x-requests-used": "400",
                "x-requests-remaining": "100",
            })
            status = odds_checker.get_quota_status()
        self.assertFalse(status["paused"])

    def test_feed_selection_rotates_extra_tennis_feeds(self):
        feeds = [
            ("basketball_nba", "NBA"),
            ("tennis_atp_a", "Tênis ATP"),
            ("tennis_atp_b", "Tênis ATP"),
            ("americanfootball_nfl", "NFL"),
        ]
        first = selecionar_feeds_para_ciclo(feeds, 6)
        second = selecionar_feeds_para_ciclo(feeds, 6)
        first_atp = [key for key, sport in first if sport == "Tênis ATP"]
        second_atp = [key for key, sport in second if sport == "Tênis ATP"]
        self.assertEqual(first_atp, ["tennis_atp_a"])
        self.assertEqual(second_atp, ["tennis_atp_b"])
        self.assertEqual(len(first), 3)




if __name__ == "__main__":
    unittest.main()
