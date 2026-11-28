import unittest
from unittest.mock import AsyncMock, patch

import bot_telegram


class SportsTelegramCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_nba_command_runs_matchup_analysis(self):
        result = {"status": "ok", "sport": "NBA"}
        with patch.object(bot_telegram, "analisar_confronto", new=AsyncMock(return_value=result)) as analyze, \
             patch.object(bot_telegram, "formatar_analise_sports", return_value="NBA analysis"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/nba boston celtics vs los angeles lakers")
        analyze.assert_awaited_once_with("NBA", "boston celtics", "los angeles lakers")
        send.assert_awaited_once_with("NBA analysis")

    async def test_nfl_command_runs_matchup_analysis(self):
        result = {"status": "ok", "sport": "NFL"}
        with patch.object(bot_telegram, "analisar_confronto", new=AsyncMock(return_value=result)) as analyze, \
             patch.object(bot_telegram, "formatar_analise_sports", return_value="NFL analysis"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/nfl kansas city chiefs vs buffalo bills")
        analyze.assert_awaited_once_with("NFL", "kansas city chiefs", "buffalo bills")
        send.assert_awaited_once_with("NFL analysis")

    async def test_nba_schedule_command_accepts_date(self):
        with patch.object(bot_telegram, "listar_jogos", new=AsyncMock(return_value={"sport":"NBA","season":"2026-27","events":[]})) as games, \
             patch.object(bot_telegram, "formatar_jogos", return_value="NBA schedule"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/jogosnba 2026-11-28")
        games.assert_awaited_once_with("NBA", "2026-11-28")
        send.assert_awaited_once_with("NBA schedule")

    async def test_sportsquota_command_explains_public_espn_source(self):
        with patch.object(bot_telegram, "formatar_status_fontes", return_value="ESPN source status"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/sportsquota")
        send.assert_awaited_once_with("ESPN source status")

    async def test_help_contains_nba_nfl_and_tennis_commands(self):
        with patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/ajuda")
        message = send.await_args.args[0]
        for command in ("/nba", "/nfl", "/jogosnba", "/jogosnfl", "/sportsquota", "/tenis", "/h2h", "/prejogo"):
            self.assertIn(command, message)


if __name__ == "__main__":
    unittest.main()
