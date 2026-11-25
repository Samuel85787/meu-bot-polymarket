import unittest

import health_status


class HealthStatusTests(unittest.TestCase):
    def setUp(self):
        health_status._services.clear()

    def test_formats_known_and_missing_services(self):
        health_status.set_service("clob_ws", "ok", "2 tokens")
        report = health_status.format_status()
        self.assertIn("Polymarket CLOB: OK", report)
        self.assertIn("2 tokens", report)
        self.assertIn("Odds API: ⚪ Sem dados", report)

    def test_age_is_reported(self):
        health_status.set_service("sports_ws", "error", "desconectado")
        item = health_status.get_services()["sports_ws"]
        self.assertGreaterEqual(item["age_seconds"], 0)
        self.assertEqual(item["state"], "error")


if __name__ == "__main__":
    unittest.main()
