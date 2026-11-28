import unittest

from signal_quality import classificar_fluxo, classificar_qualidade_odds


class SignalQualityTests(unittest.TestCase):
    def test_repeated_buy_flow_is_directional_only_with_clear_dominance(self):
        result = classificar_fluxo(
            buy_flow=4200, sell_flow=500, buy_count=4, sell_count=1,
            min_flow_usd=1000,
        )
        self.assertEqual(result["direction"], "COMPRA")
        self.assertEqual(result["label"], "Fluxo comprador recorrente")
        self.assertTrue(result["recurrent"])

    def test_balanced_flow_is_not_mislabeled_as_buy_pressure(self):
        result = classificar_fluxo(
            buy_flow=3000, sell_flow=2800, buy_count=4, sell_count=4,
            min_flow_usd=1000,
        )
        self.assertEqual(result["direction"], "MISTO")
        self.assertEqual(result["label"], "Movimentação dividida")
        self.assertFalse(result["recurrent"])

    def test_single_large_trade_is_not_called_recurrent_flow(self):
        result = classificar_fluxo(
            buy_flow=900, sell_flow=0, buy_count=1, sell_count=0,
            min_flow_usd=1000, single_trade_usd=1500,
        )
        self.assertEqual(result["direction"], "ISOLADA")
        self.assertEqual(result["label"], "Aposta individual de alto valor")

    def test_odds_quality_is_about_execution_conditions(self):
        result = classificar_qualidade_odds(
            {
                "net_ev_pct": 12, "spread_pct": 2, "slippage_pct": 0.5,
                "top_ask_depth_usd": 250, "book_age_seconds": 3,
            },
            minimum_edge_pct=5, max_spread_pct=8, max_slippage_pct=3,
            stake_usd=100, max_book_age_seconds=30,
        )
        self.assertEqual(result["label"], "Muito boa")
        self.assertGreaterEqual(result["score"], 6)

    def test_missing_execution_data_is_not_scored(self):
        result = classificar_qualidade_odds(
            {"net_ev_pct": 10}, 5, 8, 3, 100, 30
        )
        self.assertEqual(result["label"], "Dados insuficientes")
        self.assertEqual(result["score"], 0)


if __name__ == "__main__":
    unittest.main()
