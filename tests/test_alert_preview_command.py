import unittest
from unittest.mock import AsyncMock, patch

import bot_telegram


class AlertPreviewCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_shows_three_demo_layouts_without_saving_alerts(self):
        with patch.object(bot_telegram, "enviar_mensagem", new=AsyncMock()) as send:
            await bot_telegram.processar_comando("/previa")

        send.assert_awaited_once()
        message = send.await_args.args[0]
        self.assertIn("PRÉVIA VISUAL", message)
        self.assertIn("PREÇO EM MOVIMENTO", message)
        self.assertIn("ORDEM DE GRANDE VOLUME", message)
        self.assertIn("OPORTUNIDADE DE VALOR", message)
        self.assertIn("6.99x → 6.25x", message)
        self.assertIn("Pinnacle: 1.50", message)
        self.assertIn("Valores fictícios", message)


if __name__ == "__main__":
    unittest.main()
