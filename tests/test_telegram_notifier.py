import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import telegram_notifier


class FakeResponse:
    status = 200

    async def json(self, content_type=None):
        return {"ok": True}


class FakePost:
    async def __aenter__(self):
        return FakeResponse()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json):
        self.calls.append({"url": url, "json": json})
        return FakePost()


class TelegramNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_safe_html_and_inline_market_button(self):
        fake = FakeSession()
        with patch.object(telegram_notifier, "_session", fake), \
             patch.object(telegram_notifier, "_lock", asyncio.Lock()), \
             patch.object(telegram_notifier, "_last_send_time", 0.0), \
             patch.object(telegram_notifier, "iniciar_notificador", new=AsyncMock()), \
             patch.object(telegram_notifier.config, "TELEGRAM_BOT_TOKEN", "test-token"), \
             patch.object(telegram_notifier.config, "TELEGRAM_CHAT_ID", "123"):
            sent = await telegram_notifier.send_alert(
                "🚨 ALERTA DE TESTE\nSeleção: A < B & C",
                button_url="https://polymarket.com/event/test",
            )

        self.assertTrue(sent)
        self.assertEqual(len(fake.calls), 1)
        payload = fake.calls[0]["json"]
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("<b>🚨 ALERTA DE TESTE</b>", payload["text"])
        self.assertIn("A &lt; B &amp; C", payload["text"])
        self.assertEqual(
            payload["reply_markup"]["inline_keyboard"][0][0]["url"],
            "https://polymarket.com/event/test",
        )

    async def test_does_not_add_button_for_unsafe_url(self):
        fake = FakeSession()
        with patch.object(telegram_notifier, "_session", fake), \
             patch.object(telegram_notifier, "_lock", asyncio.Lock()), \
             patch.object(telegram_notifier, "_last_send_time", 0.0), \
             patch.object(telegram_notifier, "iniciar_notificador", new=AsyncMock()), \
             patch.object(telegram_notifier.config, "TELEGRAM_BOT_TOKEN", "test-token"), \
             patch.object(telegram_notifier.config, "TELEGRAM_CHAT_ID", "123"):
            sent = await telegram_notifier.send_alert(
                "Mensagem simples", button_url="javascript:alert(1)"
            )

        self.assertTrue(sent)
        self.assertNotIn("reply_markup", fake.calls[0]["json"])


if __name__ == "__main__":
    unittest.main()
