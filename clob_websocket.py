import asyncio
import datetime
import html
import json
import logging
import math
import time

import websockets

import config
from price_tracker import PriceTracker
from sports_score_checker import obter_placar_mercado
from telegram_notifier import send_alert

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketMonitor:
    def __init__(self, markets):
        self.markets = markets
        self.token_to_market = {}
        self.token_to_outcome = {}
        self.connected = False

        for market in markets:
            token_ids = market.get("clobTokenIds", [])
            outcomes = market.get("outcomes", [])

            for i, token_id in enumerate(token_ids):
                token_id = str(token_id)
                self.token_to_market[token_id] = market
                self.token_to_outcome[token_id] = (
                    outcomes[i] if i < len(outcomes) else "Desconhecido"
                )

        self.tracker = PriceTracker(
            threshold=config.PRICE_SPIKE_THRESHOLD,
            window=config.PRICE_SPIKE_WINDOW,
        )

        self.last_alert_time = {}
        self.last_whale_alert_global = 0

    def should_alert(self, key, cooldown_seconds):
        agora = time.time()
        ultimo = self.last_alert_time.get(key, 0)
        if agora - ultimo < cooldown_seconds:
            return False
        self.last_alert_time[key] = agora
        return True

    def is_market_finished(self, market):
        end_date_str = market.get("endDate")
        if not end_date_str:
            return False
        try:
            end_date_str = end_date_str.replace("Z", "+00:00")
            end_date = datetime.datetime.fromisoformat(end_date_str)
            now = datetime.datetime.now(datetime.timezone.utc)
            return now > (end_date + datetime.timedelta(hours=3))
        except Exception:
            return False

    def gerar_link(self, market):
        events = market.get("events") or []
        if events and isinstance(events[0], dict):
            event_slug = events[0].get("slug")
            if event_slug:
                return f"{config.POLYMARKET_BASE_URL}/event/{event_slug}"
        market_slug = market.get("slug")
        if market_slug:
            return f"{config.POLYMARKET_BASE_URL}/market/{market_slug}"
        return config.POLYMARKET_BASE_URL

    def parse_float(self, value, default=0.0):
        try:
            numero = float(value)
            if not math.isfinite(numero):
                return default
            return numero
        except (TypeError, ValueError):
            return default

    def market_type_label(self, market):
        market_type = str(market.get("sportsMarketType") or "Desconhecido")
        market_type_cap = market_type.capitalize()
        if market_type.lower() == "moneyline":
            odd_label = "Odd de Vitoria"
        else:
            odd_label = "Odd (" + market_type_cap + ")"
        return market_type_cap, odd_label

    def side_label(self, side_raw):
        if str(side_raw).upper() == "BUY":
            return "COMPRA (A Favor)"
        if str(side_raw).upper() == "SELL":
            return "VENDA (Contra)"
        return "Desconhecido"

    async def run(self):
        while True:
            try:
                async with websockets.connect(
                    WS_URL,
                    ping_interval=20,
                    close_timeout=10,
                ) as ws:
                    assets = list(self.token_to_market.keys())
                    sub_msg = {
                        "type": "market",
                        "assets_ids": assets,
                        "initial_dump": True,
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Conectado ao CLOB. Monitorando " + str(len(assets)) + " tokens.")
                    self.connected = True

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

            except websockets.exceptions.ConnectionClosed:
                logger.warning("Conexao WebSocket fechada. Reconectando em 5s...")
                self.connected = False
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("Erro na conexao: " + str(type(e).__name__) + ": " + str(e))
                self.connected = False
                await asyncio.sleep(5)

    async def handle_trade(self, data):
        try:
            token_id = str(data.get("asset_id", ""))
            price = self.parse_float(data.get("price", 0))

            if token_id not in self.token_to_market:
                return

            market = self.token_to_market[token_id]
            outcome_name = self.token_to_outcome.get(token_id, "Desconhecido")

            if self.is_market_finished(market):
                return

            if price <= 0 or price >= 1:
                return

            result = self.tracker.add_price(token_id, price)
            if result:
                event_type, old_price, new_price, pct = result
                if pct > 0:
                    if self.should_alert("spike_" + market.get("slug", ""), config.SPIKE_COOLDOWN_SECONDS):
                        await self.enviar_alerta_oscilacao(market, outcome_name, old_price, new_price, pct)

            if config.ENABLE_WHALE_ALERTS:
                await self.detectar_baleia(data, market, outcome_name, price)

        except Exception as e:
            logger.error("Erro ao processar trade: " + str(type(e).__name__) + ": " + str(e))

    async def enviar_alerta_oscilacao(self, market, outcome_name, old_price, new_price, pct):
        try:
            link = self.gerar_link(market)
            volume_total = self.parse_float(market.get("volume", 0))
            liquidez = self.parse_float(market.get("liquidity", 0))
            market_type, odd_label = self.market_type_label(market)
            question = market.get("question", "Mercado desconhecido")

            msg = (
                "PRECO SUBIU (Odd Caiu)\n"
                "Jogo: " + question + "\n"
                "Palpite: " + outcome_name + "\n"
                "Tipo: " + market_type + "\n"
                "Preco: " + str(round(old_price, 4)) + " -> " + str(round(new_price, 4)) + "\n"
                "Odd: " + str(round(1/old_price, 2)) + " -> " + str(round(1/new_price, 2)) + "\n"
                "Oscilacao: +" + str(round(pct*100, 2)) + "%\n"
                <span class="ml-2" /><span data-testid="markdown-streaming-circle" class="inline-block w-3 h-3 rounded-full bg-neutral-a12 align-middle mb-[0.1rem]" />