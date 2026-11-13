import asyncio
import json
import time
import datetime
import websockets
import config
from telegram_notifier import send_alert
from price_tracker import PriceTracker
from sports_score_checker import obter_placar  # <-- importa o módulo de placar

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

class PolymarketMonitor:
    # ... (o __init__ permanece igual) ...

    async def handle_trade(self, data):
        token_id = data.get("asset_id")
        price = float(data.get("price", 0) or 0)
        
        if token_id not in self.token_to_market:
            return
            
        market = self.token_to_market[token_id]
        outcome_name = self.token_to_outcome.get(token_id, "Desconhecido")

        if self.is_market_finished(market):
            return

        if price > config.MAX_PRICE or price < config.MIN_PRICE:
            return

        # ... (bloco de detecção de oscilação de preço permanece igual) ...

        # ==========================================
        # 2. DETECÇÃO DE BALEIA / INSIDER (com contexto de placar)
        # ==========================================
        if not config.ENABLE_WHALE_ALERTS:
            return

        size = float(data.get("size", 0) or 0)
        usd_value = size * price
        liquidez = float(market.get("liquidity", 0) or 0)
        
        ratio_liquidez = (usd_value / liquidez) if liquidez > 0 else 0

        is_big_bet = usd_value >= config.MIN_BET_AMOUNT
        is_insider_bet = ratio_liquidez >= config.WHALE_LIQUIDITY_RATIO

        if not is_big_bet and not is_insider_bet:
            return

        agora = time.time()
        if agora - self.last_whale_alert_global < 600: 
            return

        if not self.should_alert(f"whale_{market['slug']}", cooldown_minutes=15):
            return

        self.last_whale_alert_global = agora

        # 🆕 CONTEXTO DE PLACAR: busca o gameId do mercado e consulta o cache
        game_id = market.get("gameId") or market.get("sportsMarketType")  # ajuste se necessário
        placar = obter_placar(game_id) if game_id else None

        volume_total = float(market.get("volume", 0) or 0)
        market_type = market.get("sportsMarketType", "Desconhecido").capitalize()
        odd_label = "Odd de Vitória" if market_type == "Moneyline" else f"Odd ({market_type})"

        side_raw = data.get("side", "?")
        side_texto = "🟢 COMPRA (A Favor)" if side_raw == "BUY" else "🔴 VENDA (Contra)"
        link = self.gerar_link(market)

        # 🆕 FORMATAÇÃO COM BASE NO PLACAR
        if is_insider_bet:
            titulo = "🎯🔥 *ALERTA DE INSIDER (Baixa Liquidez)!* 🔥🎯"
            leitura = "⚠️ _A aposta engoliu parte gigante do mercado. MUITO provável que seja informação interna._"
            valor_formatado = f"💎 *${usd_value:,.2f}* ({ratio_liquidez*100:.1f}% da Liquidez)"
        elif usd_value >= config.MEGA_BET_AMOUNT:
            titulo = "🚨🔥 *MEGA BALEIA NA ÁREA!* 🔥🚨"
            leitura = "⚠️ _Atenção: Volume fora do comum. Pode ser insider._"
            valor_formatado = f"💎 *${usd_value:,.2f}*"
        else:
            titulo = "🐋 *Aposta Grande Detectada*"
            leitura = "💡 _Dinheiro inteligente entrando no mercado._"
            valor_formatado = f"💵 *${usd_value:,.2f}*"

        # Monta o bloco de placar, se disponível
        bloco_placar = ""
        if placar:
            score = placar.get("score", "")
            period = placar.get("period", "")
            status = placar.get("status", "")
            live = placar.get("live", False)
            ended = placar.get("ended", False)

            if ended:
                status_texto = "Encerrado"
            elif live:
                status_texto = f"Ao Vivo ({period})" if period else "Ao Vivo"
            else:
                status_texto = "Pré-jogo"

            bloco_placar = (
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📊 *Placar:* {score}\n"
                f"⏱️ *Status:* {status_texto}\n"
            )

            # 🆕 Lógica de "Recuperação" vs "Confirmação"
            if score:
                # Exemplo de score: "12-6" ou "Lakers 12 - Warriors 6"
                # Tenta extrair os números para comparar
                numeros = [int(s) for s in score.replace("-", " ").split() if s.isdigit()]
                if len(numeros) >= 2:
                    # Assume que o primeiro número é do time da casa (ou do outcome)
                    # Simplificação: se o time do palpite estiver perdendo por muito, marca como recuperação
                    # (Em produção, você precisaria mapear corretamente qual número é de qual time)
                    if numeros[0] > numeros[1] * 1.5:  # Ex: 12 vs 6
                        titulo = "⚠️ *APOSTA EM RECUPERAÇÃO (Risco Alto)*"
                        leitura = "💡 _O time está perdendo por uma diferença grande. A aposta pode ser desespero, não informação._"

        msg = (
            f"{titulo}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏆 *Jogo:* {market['question']}\n"
            f"🎯 *Palpite:* `{outcome_name}`\n"
            f"📌 *Tipo:* {market_type}\n"
            f"🔄 *Ação:* {side_texto}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{valor_formatado}\n"
            f"📊 *Preço:* {price:.2f} | *{odd_label}:* {1/price:.2f}\n"
            f"{bloco_placar}"  # <-- bloco de placar entra aqui
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Volume do Mercado:* ${volume_total:,.0f}\n"
            f"💧 *Liquidez Total:* ${liquidez:,.0f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{leitura}\n\n"
            f"[👉 ABRIR NA POLYMARKET]({link})"
        )
        await send_alert(msg)
