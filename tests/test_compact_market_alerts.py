import unittest
from unittest.mock import AsyncMock, patch

import clob_websocket
from clob_websocket import PolymarketMonitor


class CompactMarketAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_movement_alert_is_compact_and_shows_contract_return(self):
        monitor = PolymarketMonitor([])
        monitor.gerar_link = lambda market: "https://polymarket.com/event/test"
        monitor.titulo_evento_curto = lambda market: "Revis e FORZE Reload"
        monitor.formatar_placar = lambda score: "AO VIVO · BO3 · mapa 2/3"

        with patch.object(clob_websocket, "obter_placar_mercado", return_value={"live": True}), \
             patch.object(clob_websocket, "send_alert", new=AsyncMock(return_value=True)) as send, \
             patch.object(clob_websocket, "record_alert"):
            await monitor.enviar_alerta_oscilacao(
                {"_sport": "CS2", "liquidity": 11345},
                "FORZE Reload", 0.143, 0.160, 0.123,
                "SPIKE_UP", "token-1",
            )

        message = send.await_args.args[0]
        self.assertIn("PREÇO EM MOVIMENTO", message)
        self.assertIn("0.143  →  0.160  (+12.3%)", message)
        self.assertIn("6.99x  →  6.25x", message)
        self.assertIn("retorno bruto antes de taxas", message)
        self.assertLess(len(message), 700)
        self.assertEqual(send.await_args.kwargs["button_label"], "Ver mercado ↗")

    async def test_whale_alert_shows_contract_multiplier_and_compact_flow(self):
        monitor = PolymarketMonitor([])
        monitor.gerar_link = lambda market: "https://polymarket.com/event/test"
        monitor.titulo_evento_curto = lambda market: "Thunder x Bucks"
        with patch.object(clob_websocket, "send_alert", new=AsyncMock(return_value=True)) as send, \
             patch.object(clob_websocket, "record_alert"):
            await monitor.enviar_alerta_baleia(
                {"_sport": "NBA", "liquidity": 50000},
                "Thunder", 0.840, "BUY", 1680, "AO VIVO · Q2",
                "Compras $1,978 (3) · vendas $0 (0) · saldo +$1,978",
                "Bid 0.830 · Ask 0.840 · Spread 0.010",
                "token-1",
            )

        message = send.await_args.args[0]
        self.assertIn("ORDEM DE GRANDE VOLUME", message)
        self.assertIn("retorno bruto 1.19x", message)
        self.assertIn("Compras $1,978 (3)", message)
        self.assertIn("Bid 0.830 · Ask 0.840 · Spread 0.010", message)
        self.assertLess(len(message), 800)


if __name__ == "__main__":
    unittest.main()
