import unittest
from unittest.mock import AsyncMock, patch

import tennis_api


class TennisApiTests(unittest.TestCase):
    def test_probabilidade_elo_e_simetrica(self):
        prob_a = tennis_api.probabilidade_elo(2000, 1600)
        prob_b = tennis_api.probabilidade_elo(1600, 2000)
        self.assertAlmostEqual(prob_a, 0.9091, places=3)
        self.assertAlmostEqual(prob_a + prob_b, 1.0, places=10)

    def test_forma_recente_usa_ate_cinco_jogos(self):
        perfil = {
            "player": {"player_id": "a"},
            "recent_matches": [
                {"match_date": "2026-11-20", "winner_id": "a"},
                {"match_date": "2026-11-19", "winner_id": "b"},
                {"match_date": "2026-11-18", "winner_id": "a"},
                {"match_date": "2026-11-17", "winner_id": "a"},
                {"match_date": "2026-11-16", "winner_id": "b"},
                {"match_date": "2026-11-15", "winner_id": "a"},
            ],
        }
        forma = tennis_api._forma_recente(perfil)
        self.assertEqual(forma["matches"], 5)
        self.assertEqual(forma["wins"], 3)
        self.assertEqual(forma["losses"], 2)
        self.assertEqual(forma["last_match"], "2026-11-20")

    def test_analise_formata_elo_superficie_h2h_e_alerta(self):
        analise = {
            "status": "ok",
            "league": "tennis",
            "surface": "clay",
            "surface_name": "Saibro",
            "elo_source": "clay",
            "elo_probability_a": 0.60,
            "elo_probability_b": 0.40,
            "player_a": {
                "name": "Jogador A", "elo": 1800,
                "profile": {"player": {}, "ranking": {}},
                "surface_record": {"wins": 30, "losses": 10, "win_pct": 0.75},
                "recent_form": {"matches": 5, "wins": 4, "losses": 1, "last_match": "2026-11-20"},
                "elo_date": "2026-11-20", "elo_age_days": 8,
            },
            "player_b": {
                "name": "Jogador B", "elo": 1729,
                "profile": {"player": {}, "ranking": {}},
                "surface_record": {"wins": 20, "losses": 20, "win_pct": 0.5},
                "recent_form": {"matches": 5, "wins": 2, "losses": 3, "last_match": "2026-11-19"},
                "elo_date": "2026-11-19", "elo_age_days": 9,
            },
            "h2h": {
                "total": 5, "wins_a": 3, "wins_b": 2,
                "surface_wins_a": 2, "surface_wins_b": 1, "surface_matches": 3,
            },
        }
        texto = tennis_api.formatar_analise_matchup(
            analise, reference_prob=0.52, selected_player="Jogador A"
        )
        self.assertIn("Elo por superfície", texto)
        self.assertIn("Diferença Elo x probabilidade sem vig", texto)
        self.assertIn("2 x 1", texto)
        self.assertIn("não altera o EV", texto)

    def test_h2h_small_surface_sample_is_not_overstated(self):
        analise = {
            "status": "ok", "league": "wta", "surface": "grass",
            "surface_name": "Grama", "elo_source": "geral",
            "elo_probability_a": 0.5, "elo_probability_b": 0.5,
            "player_a": {"name": "A", "elo": 1500, "profile": {}, "surface_record": {},
                         "recent_form": {"matches": 0}, "elo_age_days": None},
            "player_b": {"name": "B", "elo": 1500, "profile": {}, "surface_record": {},
                         "recent_form": {"matches": 0}, "elo_age_days": None},
            "h2h": {"total": 1, "wins_a": 1, "wins_b": 0,
                    "surface_wins_a": 1, "surface_wins_b": 0, "surface_matches": 1},
        }
        texto = tennis_api.formatar_analise_matchup(analise)
        self.assertIn("Amostra pequena neste piso", texto)
        self.assertIn("Elo geral", texto)

    def test_normaliza_nome_ignora_acentos_e_espacos(self):
        self.assertEqual(tennis_api._normalizar_nome("  João   Fonseca "), "joao fonseca")

    def test_escolhe_jogador_por_nome_exato(self):
        players = [
            {"player_id": "1", "name": "Alexander Zverev"},
            {"player_id": "2", "name": "Alexander Bublik"},
        ]
        status, jogador = tennis_api._escolher_jogador(players, "alexander zverev")
        self.assertEqual(status, "ok")
        self.assertEqual(jogador["player_id"], "1")

    def test_retorna_ambiguo_quando_nome_nao_e_exato(self):
        players = [
            {"player_id": "1", "name": "Alexander Zverev"},
            {"player_id": "2", "name": "Alexander Bublik"},
        ]
        status, candidatos = tennis_api._escolher_jogador(players, "alexander")
        self.assertEqual(status, "ambiguous")
        self.assertEqual(len(candidatos), 2)

    def test_perfil_formata_elo_por_superficie_e_forma(self):
        perfil = {
            "league": "tennis",
            "player": {"player_id": "10", "name": "Joao Fonseca", "country": "BRA", "plays": "right"},
            "ranking": {"rank": 24},
            "rating": {
                "overall": 1900,
                "by_surface": {
                    "hard": {"rating": 1880, "matches": 30},
                    "clay": {"rating": 1920, "matches": 20},
                },
            },
            "record": {"wins": 40, "losses": 20, "win_pct": 0.666, "titles": 2},
            "recent_matches": [
                {
                    "player1_id": "10", "player1": "Joao Fonseca",
                    "player2": "Rival", "winner_id": "10",
                    "score": "6-4 6-3", "match_date": "2026-11-01",
                }
            ],
        }
        texto = tennis_api.formatar_perfil(perfil)
        self.assertIn("Elo geral: 1900", texto)
        self.assertIn("Quadra rápida: 1880", texto)
        self.assertIn("Saibro: 1920", texto)
        self.assertIn("Rival", texto)
        self.assertIn("Fonte atualizada diariamente", texto)

    def test_ambiguidades_h2h_mostra_nome_e_pais_dos_candidatos(self):
        resultado = {
            "status_a": "ambiguous",
            "player_a": [
                {"name": "Jannik Sinner", "country": "Italy"},
                {"name": "Other Sinner", "country": "Germany"},
            ],
            "status_b": "ok",
            "player_b": {"name": "Alexander Zverev"},
        }
        texto = tennis_api.formatar_ambiguidades_h2h(resultado, "sinner", "zverev")
        self.assertIn("Jogador 1", texto)
        self.assertIn("Jannik Sinner (Italy)", texto)
        self.assertIn("Other Sinner (Germany)", texto)
        self.assertNotIn("Jogador 2", texto)

    def test_h2h_formata_vitorias_por_superficie(self):
        resultado = {
            "players": [
                {"player_id": "1", "name": "Jogador A"},
                {"player_id": "2", "name": "Jogador B"},
            ],
            "summary": {
                "matches": 4,
                "wins": {"1": 3, "2": 1},
                "sets_won": {"1": 7, "2": 4},
                "by_surface": {"clay": {"1": 2, "2": 0}},
            },
            "matches": [],
        }
        texto = tennis_api.formatar_confronto(resultado)
        self.assertIn("Jogador A: 3", texto)
        self.assertIn("Saibro: Jogador A 2 x 0 Jogador B", texto)


class TennisApiAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_buscar_perfil_resolve_id_e_consulta_perfil(self):
        respostas = [
            {"players": [{"player_id": "3623", "name": "Jannik Sinner"}]},
            {"player": {"player_id": "3623", "name": "Jannik Sinner"}, "ranking": {"rank": 1}},
        ]
        with patch.object(tennis_api, "_request", new=AsyncMock(side_effect=respostas)) as request:
            resultado = await tennis_api.buscar_perfil("Jannik Sinner", "tennis")
        self.assertEqual(resultado["status"], "ok")
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.await_args_list[1].args[0], "get_player")
        self.assertEqual(request.await_args_list[1].args[1]["player_id"], "3623")

    async def test_analisar_matchup_busca_perfis_elo_e_h2h(self):
        from datetime import datetime, timezone
        hoje = datetime.now(timezone.utc).date().isoformat()
        base_a = {
            "player": {"player_id": "1", "name": "Player A"},
            "rating": {"overall": 1800, "as_of": hoje + "T12:00:00Z",
                       "by_surface": {"hard": {"rating": 1820, "matches": 80}}},
            "by_surface": [{"surface": "hard", "wins": 60, "losses": 20, "win_pct": 0.75}],
            "recent_matches": [{"match_date": hoje, "winner_id": "1"}],
        }
        base_b = {
            "player": {"player_id": "2", "name": "Player B"},
            "rating": {"overall": 1700, "as_of": hoje + "T12:00:00Z",
                       "by_surface": {"hard": {"rating": 1750, "matches": 70}}},
            "by_surface": [{"surface": "hard", "wins": 40, "losses": 30, "win_pct": 0.571}],
            "recent_matches": [{"match_date": hoje, "winner_id": "2"}],
        }
        respostas = [
            {"players": [{"player_id": "1", "name": "Player A"}]},
            {"players": [{"player_id": "2", "name": "Player B"}]},
            base_a,
            base_b,
            {"summary": {"matches": 3, "wins": {"1": 2, "2": 1},
                         "by_surface": {"hard": {"1": 2, "2": 1}}}},
        ]
        with patch.object(tennis_api, "_request", new=AsyncMock(side_effect=respostas)) as request:
            analise = await tennis_api.analisar_matchup("Player A", "Player B", surface="hard")
        self.assertEqual(analise["status"], "ok")
        self.assertEqual(analise["elo_source"], "hard")
        self.assertEqual(analise["h2h"]["surface_matches"], 3)
        self.assertEqual(request.await_count, 5)
        self.assertEqual(request.await_args_list[-1].args[1]["player_id"], "1")

    async def test_analise_faz_fallback_elo_geral_se_amostra_piso_pequena(self):
        base_a = {"player": {"player_id": "1", "name": "A"},
                  "rating": {"overall": 1800, "by_surface": {"clay": {"rating": 1900, "matches": 4}}}}
        base_b = {"player": {"player_id": "2", "name": "B"},
                  "rating": {"overall": 1700, "by_surface": {"clay": {"rating": 1600, "matches": 90}}}}
        respostas = [
            {"players": [{"player_id": "1", "name": "A"}]},
            {"players": [{"player_id": "2", "name": "B"}]},
            base_a, base_b, {"summary": {"matches": 0, "wins": {}}},
        ]
        with patch.object(tennis_api, "_request", new=AsyncMock(side_effect=respostas)):
            analise = await tennis_api.analisar_matchup("A", "B", surface="clay")
        self.assertEqual(analise["elo_source"], "geral")
        self.assertAlmostEqual(analise["elo_probability_a"], tennis_api.probabilidade_elo(1800, 1700))

    async def test_nao_consulta_o_mesmo_jogador_no_h2h(self):
        respostas = [
            {"players": [{"player_id": "1", "name": "Jogador"}]},
            {"players": [{"player_id": "1", "name": "Jogador"}]},
        ]
        with patch.object(tennis_api, "_request", new=AsyncMock(side_effect=respostas)) as request:
            resultado = await tennis_api.buscar_confronto("Jogador", "Jogador")
        self.assertEqual(resultado["status"], "same_player")
        self.assertEqual(request.await_count, 2)


if __name__ == "__main__":
    unittest.main()
