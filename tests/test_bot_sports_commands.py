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
        with patch.object(bot_telegram, "listar_jogos", new=AsyncMock(return_value=("2026-11-28", []))) as games, \
             patch.object(bot_telegram, "formatar_jogos", return_value="NBA schedule"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/jogosnba 2026-11-28")
        games.assert_awaited_once_with("NBA", "2026-11-28")
        send.assert_awaited_once_with("NBA schedule")

    async def test_sportsquota_command_formats_daily_usage(self):
        with patch.object(bot_telegram, "obter_cotas", new=AsyncMock(return_value={"NBA": {}, "NFL": {}})) as quotas, \
             patch.object(bot_telegram, "formatar_cotas", return_value="Quota status"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/sportsquota")
        quotas.assert_awaited_once()
        send.assert_awaited_once_with("Quota status")

    async def test_help_contains_api_sports_commands(self):
        with patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/ajuda")
        message = send.await_args.args[0]
        for command in ("/nba", "/nfl", "/jogosnba", "/jogosnfl", "/sportsquota"):
            self.assertIn(command, message)


if __name__ == "__main__":
    unittest.main()
