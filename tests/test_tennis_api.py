import unittest
from unittest.mock import AsyncMock, patch

import tennis_api


class TennisApiTests(unittest.TestCase):
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
        self.assertIn("Quadra rápida 1880", texto)
        self.assertIn("Saibro 1920", texto)
        self.assertIn("Rival", texto)
        self.assertIn("Dados estatísticos descritivos", texto)

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
