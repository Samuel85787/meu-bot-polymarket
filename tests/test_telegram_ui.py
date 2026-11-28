import html
import unittest

from telegram_ui import keyboard_url, render_message, split_message


class TelegramUiTests(unittest.TestCase):
    def test_render_message_emphasizes_title_sections_and_labels(self):
        rendered = render_message(
            "🚨 ALERTA DE VALOR\n━━━━━━━━━━━━━━━━━━━━\n"
            "💰 EXECUÇÃO\nEV líquido: +8.5%\n"
            "/nba celtics vs lakers"
        )
        self.assertIn("<b>🚨 ALERTA DE VALOR</b>", rendered)
        self.assertIn("<b>💰 EXECUÇÃO</b>", rendered)
        self.assertIn("<b>EV líquido:</b> +8.5%", rendered)
        self.assertIn("<code>/nba</code>", rendered)

    def test_dynamic_html_is_escaped(self):
        rendered = render_message('Equipe <script>alert("x")</script> & jogador')
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&amp;", rendered)

    def test_plain_url_becomes_safe_html_link(self):
        rendered = render_message("Abrir mercado: https://polymarket.com/event/test")
        self.assertIn('<a href="https://polymarket.com/event/test">Abrir link ↗</a>', rendered)

    def test_keyboard_rejects_non_http_urls(self):
        self.assertIsNone(keyboard_url("javascript:alert(1)"))
        self.assertIsNotNone(keyboard_url("https://polymarket.com/event/test"))

    def test_keyboard_label_is_limited(self):
        markup = keyboard_url("https://polymarket.com", "A" * 100)
        self.assertEqual(len(markup["inline_keyboard"][0][0]["text"]), 64)

    def test_long_messages_are_split_under_safe_raw_limit(self):
        message = "\n".join([f"line {i}" for i in range(1200)])
        chunks = split_message(message, max_chars=900)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 900 for chunk in chunks))
        self.assertEqual("\n".join(chunks), message)


if __name__ == "__main__":
    unittest.main()
