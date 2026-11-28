import unittest
from unittest.mock import AsyncMock, patch

import espn_sports


def _competitor(team_id, name, score, home_away, winner=None):
    item = {
        "team": {"id": str(team_id), "displayName": name, "name": name.split()[-1]},
        "score": {"value": str(score), "displayValue": str(score)},
        "homeAway": home_away,
    }
    if winner is not None:
        item["winner"] = winner
    return item


def _game(event_id, date, home_id, home_name, home_score, away_id, away_name, away_score):
    return {
        "id": str(event_id),
        "date": date,
        "competitions": [{
            "status": {"type": {"completed": True, "state": "post"}},
            "competitors": [
                _competitor(home_id, home_name, home_score, "home", home_score > away_score),
                _competitor(away_id, away_name, away_score, "away", away_score > home_score),
            ],
        }],
    }


class ESPNParsingTests(unittest.TestCase):
    def test_team_parser_extracts_teams_from_espn_nested_response(self):
        payload = {
            "sports": [{
                "leagues": [{
                    "teams": [
                        {"team": {"id": "1", "displayName": "Dallas Mavericks", "abbreviation": "DAL"}},
                        {"team": {"id": "2", "displayName": "Golden State Warriors", "abbreviation": "GS"}},
                    ]
                }]
            }]
        }
        teams = list(espn_sports._walk_team_nodes(payload))
        self.assertEqual(len(teams), 2)
        self.assertEqual(teams[0]["displayName"], "Dallas Mavericks")

    def test_team_resolver_accepts_full_name_and_unique_nickname(self):
        teams = [
            {"id": "1", "displayName": "Dallas Mavericks", "name": "Mavericks",
             "location": "Dallas", "abbreviation": "DAL"},
            {"id": "2", "displayName": "Golden State Warriors", "name": "Warriors",
             "location": "Golden State", "abbreviation": "GS"},
        ]
        self.assertEqual(espn_sports._resolve_team(teams, "dallas mavericks")["id"], "1")
        self.assertEqual(espn_sports._resolve_team(teams, "warriors")["id"], "2")
        self.assertIsNone(espn_sports._resolve_team(teams, "unknown team"))

    def test_current_season_is_read_from_live_scoreboard_not_hardcoded(self):
        payload = {"leagues": [{"season": {"year": 2027, "displayName": "2026-27"}}]}
        self.assertEqual(espn_sports._current_season(payload), (2027, "2026-27"))

    def test_schedule_records_only_include_completed_current_feed_games(self):
        games = [
            _game("1", "2026-11-20T00:00:00Z", "1", "Dallas Mavericks", 120,
                  "2", "Golden State Warriors", 110),
            _game("2", "2026-11-18T00:00:00Z", "2", "Golden State Warriors", 115,
                  "1", "Dallas Mavericks", 100),
            {
                "id": "3", "date": "2026-11-28T00:00:00Z",
                "competitions": [{
                    "status": {"type": {"completed": False, "state": "pre"}},
                    "competitors": [
                        _competitor("1", "Dallas Mavericks", 0, "home"),
                        _competitor("2", "Golden State Warriors", 0, "away"),
                    ],
                }],
            },
        ]
        record = espn_sports._record(games, "1")
        self.assertEqual(record["games"], 2)
        self.assertEqual(record["wins"], 1)
        self.assertEqual(record["losses"], 1)
        self.assertEqual(record["last_five_wins"], 1)
        self.assertAlmostEqual(record["avg_for"], 110.0)

    def test_h2h_uses_common_events_between_current_team_schedules(self):
        game1 = _game("1", "2026-11-20T00:00:00Z", "1", "Dallas Mavericks", 120,
                      "2", "Golden State Warriors", 110)
        game2 = _game("2", "2026-11-18T00:00:00Z", "2", "Golden State Warriors", 115,
                      "1", "Dallas Mavericks", 100)
        result = espn_sports._h2h([game1, game2], [game1, game2], "1", "2")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["wins_home"], 1)
        self.assertEqual(result["wins_away"], 1)

    def test_formatter_labels_current_season_and_data_source(self):
        analysis = {
            "status": "ok", "sport": "NBA", "season": "2026-27",
            "updated_at": "16:30",
            "home": {"name": "Dallas Mavericks", "record": {
                "wins": 10, "losses": 5, "ties": 0, "games": 15,
                "last_five": [{"outcome": "W"}, {"outcome": "L"}],
                "last_five_wins": 1, "last_five_losses": 1,
                "avg_for": 115.0, "avg_against": 111.0,
            }},
            "away": {"name": "Golden State Warriors", "record": {
                "wins": 8, "losses": 7, "ties": 0, "games": 15,
                "last_five": [{"outcome": "L"}, {"outcome": "W"}],
                "last_five_wins": 1, "last_five_losses": 1,
                "avg_for": 112.0, "avg_against": 113.0,
            }},
            "h2h": {"wins_home": 1, "wins_away": 0, "total": 1, "recent": []},
        }
        message = espn_sports.formatar_analise(analysis)
        self.assertIn("temporada atual: 2026-27", message.lower())
        self.assertIn("Fonte: ESPN", message)
        self.assertIn("Amostra pequena", message)
        self.assertIn("não altera o EV", message)

    def test_schedule_formatter_uses_clean_card_layout(self):
        data = {
            "sport": "NBA", "season": "2026-27", "date": "2026-11-28",
            "updated_at": "16:30",
            "events": [{
                "date": "2026-11-28T22:00:00Z",
                "competitions": [{
                    "status": {"type": {"shortDetail": "Hoje, 19:00"}},
                    "competitors": [
                        _competitor("1", "Washington Wizards", 0, "home"),
                        _competitor("2", "Orlando Magic", 0, "away"),
                    ],
                }],
            }],
        }
        message = espn_sports.formatar_jogos(data)
        self.assertIn("JOGOS NBA", message)
        self.assertIn("Orlando Magic", message)
        self.assertIn("Washington Wizards", message)
        self.assertIn("temporada 2026-27", message)


class ESPNAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_analisar_confronto_loads_current_season_and_both_team_schedules(self):
        home_game = _game("g1", "2026-11-20T00:00:00Z", "1", "Dallas Mavericks", 120,
                          "2", "Golden State Warriors", 110)
        away_game = _game("g1", "2026-11-20T00:00:00Z", "1", "Dallas Mavericks", 120,
                          "2", "Golden State Warriors", 110)
        async def fake_get(path, params=None, ttl=None):
            if path.endswith("/teams"):
                return {"sports": [{"leagues": [{"teams": [
                    {"team": {"id": "1", "displayName": "Dallas Mavericks", "name": "Mavericks",
                              "location": "Dallas", "abbreviation": "DAL"}},
                    {"team": {"id": "2", "displayName": "Golden State Warriors", "name": "Warriors",
                              "location": "Golden State", "abbreviation": "GS"}},
                ]}]}]}
            if path.endswith("/scoreboard"):
                return {"leagues": [{"season": {"year": 2027, "displayName": "2026-27"}}]}
            if path.endswith("/teams/dal/schedule"):
                return {"team": {"recordSummary": "1-0"}, "events": [home_game]}
            if path.endswith("/teams/gs/schedule"):
                return {"team": {"recordSummary": "0-1"}, "events": [away_game]}
            raise AssertionError(path)

        with patch.object(espn_sports, "_get", side_effect=fake_get) as get:
            result = await espn_sports.analisar_confronto("NBA", "Dallas Mavericks", "Warriors")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["season"], "2026-27")
        self.assertEqual(result["home"]["record"]["wins"], 1)
        self.assertEqual(result["away"]["record"]["losses"], 1)
        self.assertEqual(result["h2h"]["total"], 1)
        self.assertEqual(get.await_count, 4)

    async def test_analysis_refuses_to_fallback_to_old_season_when_scoreboard_has_no_season(self):
        async def fake_get(path, params=None, ttl=None):
            if path.endswith("/teams"):
                return {"sports": [{"leagues": [{"teams": [
                    {"team": {"id": "1", "displayName": "Dallas Mavericks", "abbreviation": "DAL"}},
                    {"team": {"id": "2", "displayName": "Golden State Warriors", "abbreviation": "GS"}},
                ]}]}]}
            return {"events": []}
        with patch.object(espn_sports, "_get", side_effect=fake_get):
            with self.assertRaises(espn_sports.SportsDataError):
                await espn_sports.analisar_confronto("NBA", "Dallas Mavericks", "Golden State Warriors")

    async def test_schedule_uses_current_scoreboard(self):
        scoreboard = {"leagues": [{"season": {"year": 2026, "displayName": "2026"}}],
                      "events": [{"id": "123"}]}
        with patch.object(espn_sports, "_scoreboard", new=AsyncMock(return_value=scoreboard)):
            agenda = await espn_sports.listar_jogos("NFL")
        self.assertEqual(agenda["season"], "2026")
        self.assertEqual(len(agenda["events"]), 1)


if __name__ == "__main__":
    unittest.main()
