import unittest
from unittest.mock import AsyncMock, patch

import bot_telegram


class BotStatusUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_status_is_clear_and_compact(self):
        with patch.object(bot_telegram, "monitor_ativo", False), \
             patch.object(bot_telegram, "monitor_instance", None), \
             patch.object(bot_telegram, "format_status", return_value="🟢 Telegram: comandos e envios OK\n⏸️ Polymarket CLOB e Gamma: pausados com o monitor"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/status")

        message = send.await_args.args[0]
        self.assertIn("🩺 PAINEL DO BOT", message)
        self.assertIn("Monitor: ⏸️ PAUSADO", message)
        self.assertIn("Nenhum mercado está sendo acompanhado", message)
        self.assertIn("Para começar a monitorar: /start", message)
        self.assertIn("Polymarket CLOB e Gamma: pausados", message)
        self.assertNotIn("Sem dados", message)
        self.assertLess(len(message), 1000)

    async def test_active_status_uses_compact_sport_summary(self):
        fake_monitor = type("FakeMonitor", (), {
            "markets": [
                {"_sport": "NBA"},
                {"_sport": "NBA"},
                {"_sport": "NFL"},
            ]
        })()
        with patch.object(bot_telegram, "monitor_ativo", True), \
             patch.object(bot_telegram, "monitor_instance", fake_monitor), \
             patch.object(bot_telegram, "format_status", return_value="🟢 Telegram: comandos e envios OK"), \
             patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/status")

        message = send.await_args.args[0]
        self.assertIn("Monitor: 🟢 ATIVO", message)
        self.assertIn("NBA 2", message)
        self.assertIn("NFL 1", message)
        self.assertNotIn("\n\n\n", message)


if __name__ == "__main__":
    unittest.main()
