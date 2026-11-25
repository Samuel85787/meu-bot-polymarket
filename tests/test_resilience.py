import asyncio
import unittest
from unittest.mock import patch

import gamma_api
from clob_websocket import PolymarketMonitor
from price_tracker import PriceTracker
from sports_score_checker import armazenar_update, obter_placar


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, *args, **kwargs):
        return next(self.responses)


class ResilienceTests(unittest.TestCase):
    def test_gamma_retries_rate_limit_and_then_succeeds(self):
        session = FakeSession([
            FakeResponse(429, headers={"Retry-After": "0"}),
            FakeResponse(200, payload=[{"id": "market-1"}]),
        ])
        with patch.object(gamma_api.asyncio, "sleep", new_callable=lambda: (lambda *_: asyncio.sleep(0))):
            result = asyncio.run(
                gamma_api._get_json_with_retry(session, "https://example.test", {}, "NBA")
            )
        self.assertEqual(result, [{"id": "market-1"}])

    def test_sports_feed_parses_string_booleans(self):
        armazenar_update({
            "gameId": "test-game-bool",
            "score": "1-0",
            "live": "false",
            "ended": "true",
        })
        result = obter_placar("test-game-bool")
        self.assertIsNotNone(result)
        self.assertFalse(result["live"])
        self.assertTrue(result["ended"])

    def test_market_refresh_prunes_inactive_token_state(self):
        monitor = PolymarketMonitor([
            {"clobTokenIds": ["old-token"], "outcomes": ["Yes"]}
        ])
        monitor.order_books["old-token"] = {"best_bid": 0.4}
        monitor.trade_history["old-token"].append((1.0, 100.0, "BUY"))
        monitor.tracker.add_price("old-token", 0.4)

        monitor.update_markets([
            {"clobTokenIds": ["new-token"], "outcomes": ["No"]}
        ])

        self.assertNotIn("old-token", monitor.order_books)
        self.assertNotIn("old-token", monitor.trade_history)
        self.assertNotIn("old-token", monitor.tracker.last_seen)
        self.assertEqual(set(monitor.token_to_market), {"new-token"})


if __name__ == "__main__":
    unittest.main()
