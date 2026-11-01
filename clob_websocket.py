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
        self.last_whale_alert_global = 0 

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

    def should_alert(self, key, cooldown_minutes=3):
        agora = time.time()
        ultimo_alerta = self.last_alert_time.get(key, 0)
        if agora - ultimo_alerta < (cooldown_minutes * 60):
            return False
        self.last_alert_time[key] = agora
        return True

    async def run(self):
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
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
                            # 🔥 A CORREÇÃO ESTÁ AQUI: Só processa negociações reais (last_trade_price)
                            if et == "last_trade_price":
                                await self.handle_trade(ev)
            
            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                print(f"⚠️ Conexão caiu. Reconectando em 5s...")
                await asyncio.sleep(5) 
                continue

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

        # ==========================================
        # 1. DETECÇÃO DE OSCILAÇÃO (PRICE SPIKE)
        # ==========================================
        result = self.tracker.add_price(token_id, price)
        if result:
            event_type, old_price, new_price, pct = result
            
            # Cooldown independente para o spike
            if self.should_alert(f"spike_{market['slug']}", cooldown_minutes=5):
                link = self.gerar_link(market)
                volume_total = float(market.get("volume", 0) or 0)
                liquidez = float(market.get("liquidity", 0) or 0)
                market_type = market.get("sportsMarketType", "Desconhecido").capitalize()
                odd_label = "Odd de Vitória" if market_type == "Moneyline" else f"Odd ({market_type})"

                if pct > 0:
                    titulo = "📈 *PREÇO SUBIU* (Odd Caiu)"
                    leitura = "💡 _O mercado está confiante. Alguém sabe de algo._"
                    movimento = f"🔼 `{old_price:.2f} ➔ {new_price:.2f}`"
                    odds = f"📉 `{1/old_price:.2f} ➔ {1/new_price:.2f}`"
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
                    f"📌 *Tipo:* {market_type}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📊 *Preço:* {movimento}\n"
                    f"💰 *{odd_label}:* {odds}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📈 *Volume do Mercado:* ${volume_total:,.0f}\n"
                    f"💧 *Liquidez:* ${liquidez:,.0f}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{leitura}\n\n"
                    f"[👉 ABRIR NA POLYMARKET]({link})"
                )
                await send_alert(msg)

        # ==========================================
        # 2. DETECÇÃO DE BALEIA (WHALE ALERT)
        # ==========================================
        if not config.ENABLE_WHALE_ALERTS:
            return

        size = float(data.get("size", 0) or 0)
        usd_value = size * price
        
        if usd_value < config.MIN_BET_AMOUNT:
            return

        agora = time.time()
        if agora - self.last_whale_alert_global < 600: 
            return

        # Cooldown independente para a baleia
        if not self.should_alert(f"whale_{market['slug']}", cooldown_minutes=15):
            return

        self.last_whale_alert_global = agora

        volume_total = float(market.get("volume", 0) or 0)
        liquidez = float(market.get("liquidity", 0) or 0)
        market_type = market.get("sportsMarketType", "Desconhecido").capitalize()
        odd_label = "Odd de Vitória" if market_type == "Moneyline" else f"Odd ({market_type})"

        side_raw = data.get("side", "?")
        side_texto = "🟢 COMPRA (A Favor)" if side_raw == "BUY" else "🔴 VENDA (Contra)"
        link = self.gerar_link(market)
        
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
            f"📌 *Tipo:* {market_type}\n"
            f"🔄 *Ação:* {side_texto}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{valor_formatado}\n"
            f"📊 *Preço:* {price:.2f} | *{odd_label}:* {1/price:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Volume do Mercado:* ${volume_total:,.0f}\n"
            f"💧 *Liquidez:* ${liquidez:,.0f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{leitura}\n\n"
            f"[👉 ABRIR NA POLYMARKET]({link})"
        )
        await send_alert(msg)
