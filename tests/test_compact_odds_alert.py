import unittest
from unittest.mock import AsyncMock, patch

import odds_checker


class CompactOddsValueAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_alert_separates_pinnacle_decimal_odds_from_contract_return(self):
        game = {"home_team": "Boston Celtics", "away_team": "Milwaukee Bucks"}
        info = {"name": "Boston Celtics", "odds": 1.50}
        ev_data = {
            "average_price": 0.500,
            "stake_usd": 50.0,
            "net_ev_pct": 8.5,
            "net_profit_usd": 4.25,
            "spread_pct": 1.2,
            "slippage_pct": 0.4,
            "fee_usd": 0.15,
            "top_ask_depth_usd": 1200.0,
            "book_age_seconds": 2.0,
            "gross_shares": 100.0,
            "net_shares": 99.7,
        }
        pm_market = {
            "slug": "celtics-bucks",
            "_sport": "NBA",
            "conditionId": "condition-1",
        }
        quality = {"label": "Boa", "score": 6, "max_score": 7}
        with patch.object(odds_checker, "classificar_qualidade_odds", return_value=quality), \
             patch.object(odds_checker, "formatar_placar_evento", return_value=""), \
             patch.object(odds_checker, "send_alert", new=AsyncMock(return_value=True)) as send, \
             patch.object(odds_checker, "record_alert"):
            result = await odds_checker.send_arbitrage_alert(
                game, info, 1.50, 0.6667, ev_data, pm_market, token_id="token-1"
            )

        self.assertTrue(result)
        message = send.await_args.args[0]
        self.assertIn("Pinnacle (odd decimal): 1.50", message)
        self.assertIn("Polymarket: preço 0.500  ·  retorno bruto 2.00x", message)
        self.assertIn("EV líquido: +8.5%", message)
        self.assertIn("odd decimal", message.lower())
        self.assertEqual(send.await_args.kwargs["button_label"], "Ver mercado ↗")
        self.assertNotIn("1.500", message)


if __name__ == "__main__":
    unittest.main()
