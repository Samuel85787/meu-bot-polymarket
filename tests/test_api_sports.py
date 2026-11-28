import unittest
from unittest.mock import AsyncMock, patch

import api_sports


class ApiSportsHelpersTests(unittest.TestCase):
    def setUp(self):
        for sport in ("NBA", "NFL"):
            api_sports._quota[sport].update({
                "limit": None, "remaining": None, "used": None, "updated_at": None
            })
        api_sports._cache.clear()
        api_sports._analysis_cache.clear()

    def test_nba_team_record_uses_visitors_and_nested_points(self):
        games = [
            {
                "status": {"short": 3, "long": "Finished"},
                "date": {"start": "2026-11-20T00:00:00Z"},
                "teams": {
                    "home": {"id": 10, "name": "Boston Celtics"},
                    "visitors": {"id": 20, "name": "New York Knicks"},
                },
                "scores": {
                    "home": {"points": 110}, "visitors": {"points": 99}
                },
            },
            {
                "status": {"short": 3, "long": "Finished"},
                "date": {"start": "2026-11-18T00:00:00Z"},
                "teams": {
                    "home": {"id": 20, "name": "New York Knicks"},
                    "visitors": {"id": 10, "name": "Boston Celtics"},
                },
                "scores": {
                    "home": {"points": 105}, "visitors": {"points": 101}
                },
            },
            {
                "status": {"short": 1, "long": "Not Started"},
                "date": {"start": "2026-11-25T00:00:00Z"},
                "teams": {
                    "home": {"id": 10, "name": "Boston Celtics"},
                    "visitors": {"id": 20, "name": "New York Knicks"},
                },
                "scores": {"home": {"points": None}, "visitors": {"points": None}},
            },
        ]
        record = api_sports._team_record(games, "10")
        self.assertEqual(record["games"], 2)
        self.assertEqual(record["wins"], 1)
        self.assertEqual(record["losses"], 1)
        self.assertEqual(record["last_five_wins"], 1)
        self.assertEqual(record["last_five_losses"], 1)

    def test_h2h_uses_team_ids_and_reports_recent_results(self):
        games = [
            {
                "status": {"short": 3},
                "date": {"start": "2026-11-20T00:00:00Z"},
                "teams": {
                    "home": {"id": 1, "name": "Team A"},
                    "visitors": {"id": 2, "name": "Team B"},
                },
                "scores": {"home": {"points": 110}, "visitors": {"points": 100}},
            },
            {
                "status": {"short": 3},
                "date": {"start": "2026-11-18T00:00:00Z"},
                "teams": {
                    "home": {"id": 2, "name": "Team B"},
                    "visitors": {"id": 1, "name": "Team A"},
                },
                "scores": {"home": {"points": 99}, "visitors": {"points": 105}},
            },
        ]
        result = api_sports._h2h_record(games, 1, 2)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["wins_a"], 2)
        self.assertEqual(result["wins_b"], 0)

    def test_team_resolver_requires_unique_partial_alias(self):
        teams = [
            {"id": 1, "name": "Los Angeles Lakers", "nickname": "Lakers", "code": "LAL"},
            {"id": 2, "name": "Boston Celtics", "nickname": "Celtics", "code": "BOS"},
        ]
        self.assertEqual(api_sports._resolve_team(teams, "lakers")["id"], 1)
        self.assertIsNone(api_sports._resolve_team(teams, "los angeles"))

    def test_formatter_marks_small_h2h_sample(self):
        analysis = {
            "status": "ok", "sport": "NBA", "season": 2026,
            "home": {
                "name": "Team A",
                "record": {
                    "wins": 10, "losses": 5, "games": 15,
                    "last_five": [{"win": True}, {"win": False}],
                    "last_five_wins": 1, "last_five_losses": 1,
                    "avg_for": 110.2, "avg_against": 106.1,
                },
            },
            "away": {
                "name": "Team B",
                "record": {
                    "wins": 8, "losses": 7, "games": 15,
                    "last_five": [{"win": False}],
                    "last_five_wins": 0, "last_five_losses": 1,
                    "avg_for": 104.0, "avg_against": 109.0,
                },
            },
            "h2h": {"wins_home": 1, "wins_away": 0, "total": 1, "recent": []},
        }
        text = api_sports.formatar_analise(analysis)
        self.assertIn("Campanha e forma recente", text)
        self.assertIn("Team A", text)
        self.assertIn("amostra pequena", text)

    def test_nba_local_time_formatter_converts_utc_to_recife(self):
        # Recife uses UTC-3; 22:00 UTC should display 19:00 local.
        self.assertIn("19:00 (Recife)", api_sports._hora_local("2026-11-20T22:00:00Z"))

    def test_nba_schedule_does_not_send_unsupported_timezone_param(self):
        async_result = {"response": []}
        with patch.object(api_sports, "_request", new=AsyncMock(return_value=async_result)) as request:
            import asyncio
            asyncio.run(api_sports.listar_jogos("NBA", "2026-11-28"))
        self.assertEqual(request.await_args.args[0], "NBA")
        self.assertNotIn("timezone", request.await_args.args[2])


class ApiSportsQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api_sports._cache.clear()
        for sport in ("NBA", "NFL"):
            api_sports._quota[sport].update({
                "limit": None, "remaining": None, "used": None, "updated_at": None
            })

    async def test_obter_cotas_reads_status_without_spending_paid_quota(self):
        async def fake_request(sport, endpoint, params=None, ttl=None, free_status=False):
            return {"response": {
                "requests": {"limit_day": 100, "current": 12},
                "subscription": {"plan": "Free", "active": True},
            }}
        with patch.object(api_sports, "_request", side_effect=fake_request):
            quotas = await api_sports.obter_cotas()
        self.assertEqual(quotas["NBA"]["remaining"], 88)
        self.assertEqual(quotas["NFL"]["used"], 12)
        self.assertEqual(quotas["NBA"]["plan"], "Free")

    async def test_api_request_blocks_when_daily_reserve_is_reached(self):
        api_sports._quota["NBA"].update({"limit": 100, "remaining": 10, "used": 90})
        with patch.object(api_sports.aiohttp, "ClientSession") as session:
            with self.assertRaises(api_sports.SportsAPIError):
                await api_sports._request("NBA", "teams", {"league": "standard"})
            session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
