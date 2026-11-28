import asyncio
import json
import unittest

from clob_websocket import PolymarketMonitor


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(json.loads(message))


class ClobMonitorTests(unittest.TestCase):
    def test_order_book_snapshot_and_quote_refresh(self):
        monitor = PolymarketMonitor([])
        monitor.update_order_book({
            "asset_id": "token-1",
            "bids": [{"price": "0.48", "size": "100"}, {"price": "0.47", "size": "50"}],
            "asks": [{"price": "0.51", "size": "80"}, {"price": "0.52", "size": "40"}],
        })
        book = monitor.order_books["token-1"]
        self.assertAlmostEqual(book["best_bid"], 0.48)
        self.assertAlmostEqual(book["best_ask"], 0.51)
        self.assertAlmostEqual(book["spread"], 0.03)
        self.assertAlmostEqual(book["bid_depth_5"], 150)

        monitor.update_best_quotes({
            "price_changes": [{
                "asset_id": "token-1",
                "best_bid": "0.49",
                "best_ask": "0.50",
            }]
        })
        self.assertAlmostEqual(monitor.order_books["token-1"]["spread"], 0.01)

    def test_event_title_is_shortened_without_market_rules(self):
        monitor = PolymarketMonitor([])
        market = {
            "question": "In the upcoming NFL football game between Packers and Rams, "
            "originally scheduled for Wednesday, November 25, 2026 at 8:00 PM ET.",
        }
        self.assertEqual(monitor.titulo_evento_curto(market), "Packers x Rams")

    def test_subscription_sync_adds_and_removes_tokens(self):
        monitor = PolymarketMonitor([{"clobTokenIds": ["old-token"], "outcomes": ["Yes"]}])
        monitor.connected = True
        monitor._ws = FakeWebSocket()
        monitor._subscribed_assets = {"old-token"}
        monitor.update_markets([{"clobTokenIds": ["new-token"], "outcomes": ["Yes"]}])

        asyncio.run(monitor.sync_subscriptions())

        self.assertEqual(monitor._ws.messages, [
            {"assets_ids": ["new-token"], "operation": "subscribe"},
            {"assets_ids": ["old-token"], "operation": "unsubscribe"},
        ])
        self.assertEqual(monitor._subscribed_assets, {"new-token"})


if __name__ == "__main__":
    unittest.main()
