import asyncio
import json
import time
import datetime
import websockets
import config
from telegram_notifier import send_alert
from price_tracker import PriceTracker

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

class PolymarketMonitor:
    def __init__(self, markets):
        self.markets = markets
        self.token_to_market = {}
        self.token_to_outcome = {}
        
        for m in markets:
            for i, token_id in enumerate(m["clobTokenIds"]):
                self.token_to_market[token_id] = m
                outcome_name = m["outcomes"][i] if i < len(m["outcomes"]) else "Desconhecido"
                self.token_to_outcome[token_id] = outcome_name
        
        self.tracker = PriceTracker(
            threshold=config.PRICE_SPIKE_THRESHOLD,
            window=config.PRICE_SPIKE_WINDOW,
        )
        self.last_alert_time = {}

    def is_market_finished(self, market):
        end_date_str = market.get("endDate")
        if not end_date_str:
            return False
        try:
            end_date_str = end_date_str.replace("Z", "+00:00")
            end_date = datetime.datetime.fromisoformat(end_date_str)
            end_date_with_buffer = end_date + datetime.timedelta(hours=3)
            now = datetime.datetime.now(datetime.timezone.utc)
            if now > end_date_with_buffer:
                return True
        except Exception:
            pass 
        return False

    def gerar_link(self, market):
        if "events" in market and len(market["events"]) > 0:
            event_slug = market["events"][0].get("slug")
            if event_slug:
                return f"{config.POLYMARKET_BASE_URL}/event/{event_slug}"
        market_slug = market.get("slug")
        if market_slug:
            return f"{config.POLYMARKET_BASE_URL}/market/{market_slug}"
        return config.POLYMARKET_BASE_URL

    def should_alert(self, market_slug, cooldown_minutes=3):
        agora = time.time()
        ultimo_alerta = self.last_alert_time.get(market_slug, 0)
        if agora - ultimo_alerta < (cooldown_minutes * 60):
            return False
        self.last_alert_time[market_slug] = agora
        return True

    async def run(self):
        async with websockets.connect(WS_URL) as ws:
            assets = list(self.token_to_market.keys())
            sub_msg = {
                "type": "market",
                "assets_ids": assets,
                "initial_dump": True,
            }
            await ws.send(json.dumps(sub_msg))
            print(f"🔗 Conectado. Monitorando {len(assets)} tokens.")

            async for raw in ws:
                if raw == "PONG":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                events = data if isinstance(data, list) else [data]
                for ev in events:
                    et = ev.get("event_type")
                    if et == "last_trade_price":
                        await self.handle_trade(ev)
                    elif et == "price_change":
                        await self.handle_price_change(ev)

    async def handle_trade(self, data):
        size = float(data.get("size", 0) or 0)
        price = float(data.get("price", 0) or 0)
        usd_value = size * price
        
        if usd_value < config.MIN_BET_AMOUNT:
            return
        if price > config.MAX_PRICE or price < config.MIN_PRICE:
            return

        token_id = data.get("asset_id")
        market = self.token_to_market.get(token_id)
        outcome_name = self.token_to_outcome.get(token_id, "Desconhecido")
        
        if not market or self.is_market_finished(market) or not self.should_alert(market['slug']):
            return

        side_raw = data.get("side", "?")
        side_texto = "🟢 COMPRA (A Favor)" if side_raw == "BUY" else "🔴 VENDA (Contra)"
        link = self.gerar_link(market)
        
        # 🐋 DIFERENCIAÇÃO VISUAL DE ACORDO COM O VALOR
        if usd_value >= config.MEGA_BET_AMOUNT:
            titulo = "🚨🔥 *MEGA BALEIA NA ÁREA!* 🔥🚨"
            leitura = "⚠️ _Atenção: Volume fora do comum. Pode ser insider._"
            valor_formatado = f"💎 *${usd_value:,.2f}*"
        else:
            titulo = "🐋 *Aposta Grande Detectada*"
            leitura = "💡 _Dinheiro inteligente entrando no mercado._"
            valor_formatado = f"💵 *${usd_value:,.2f}*"

        msg = (
            f"{titulo}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏆 *Jogo:* {market['question']}\n"
            f"🎯 *Palpite:* `{outcome_name}`\n"
            f"🔄 *Ação:* {side_texto}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{valor_formatado}\n"
            f"📊 *Preço:* {price:.2f} | *Odd:* {1/price:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{leitura}\n\n"
            f"[👉 ABRIR NA POLYMARKET]({link})"
        )
        await send_alert(msg)

    async def handle_price_change(self, data):
        changes = data.get("price_changes") or []
        if not changes and "price" in data and "asset_id" in data:
            changes = [data]

        self.tracker.threshold = config.PRICE_SPIKE_THRESHOLD

        for change in changes:
            token_id = change.get("asset_id")
            new_price = float(change.get("price", 0) or 0)
            
            if new_price > config.MAX_PRICE or new_price < config.MIN_PRICE:
                continue
                
            if token_id not in self.token_to_market:
                continue

            market = self.token_to_market[token_id]
            outcome_name = self.token_to_outcome.get(token_id, "Desconhecido")

            if self.is_market_finished(market):
                continue

            result = self.tracker.add_price(token_id, new_price)
            if not result:
                continue
                
            event_type, old_price, new_price, pct = result
            
            if not self.should_alert(market['slug']):
                continue

            link = self.gerar_link(market)
            
            # FORMATO VISUAL PARA PREÇO SUBINDO
            if pct > 0:
                titulo = "📈 *PREÇO SUBIU* (Odd Caiu)"
                leitura = "💡 _O mercado está confiante. Alguém sabe de algo._"
                movimento = f"🔼 `{old_price:.2f} ➔ {new_price:.2f}`"
                odds = f"📉 `{1/old_price:.2f} ➔ {1/new_price:.2f}`"
            # FORMATO VISUAL PARA PREÇO CAINDO
            else:
                titulo = "📉 *PREÇO CAIU* (Odd Subiu)"
                leitura = "💡 _Desconto! O mercado desistiu, a odd está alta._"
                movimento = f"🔽 `{old_price:.2f} ➔ {new_price:.2f}`"
                odds = f"📈 `{1/old_price:.2f} ➔ {1/new_price:.2f}`"
                
            msg = (
                f"{titulo}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏆 *Jogo:* {market['question']}\n"
                f"🎯 *Palpite:* `{outcome_name}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📊 *Preço:* {movimento}\n"
                f"💰 *Odd:* {odds}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{leitura}\n\n"
                f"[👉 ABRIR NA POLYMARKET]({link})"
            )
            await send_alert(msg)
