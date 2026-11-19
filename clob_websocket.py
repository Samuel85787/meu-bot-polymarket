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

            # buffer pequeno para evitar lixo logo após o encerramento
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
            odd_label = "Odd de Vitória"
        else:
            odd_label = f"Odd ({market_type_cap})"

        return market_type_cap, odd_label

    def side_label(self, side_raw):
        if str(side_raw).upper() == "BUY":
            return "🟢 COMPRA (A Favor)"
        if str(side_raw).upper() == "SELL":
            return "🔴 VENDA (Contra)"
        return "❓ Desconhecido"

    def formatar_placar(self, placar):
        if not placar:
            return ""

        score = html.escape(str(placar.get("score", "")))
        period = html.escape(str(placar.get("period", "")))
        elapsed = html.escape(str(placar.get("elapsed", "")))
        live = bool(placar.get("live", False))
        ended = bool(placar.get("ended", False))

        if ended:
            status = "Encerrado"
        elif live:
            extra = period or elapsed
            status = f"Ao vivo {extra}".strip()
        else:
            status = "Pré-jogo"

        return (
            "━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Placar:</b> {score or 'N/D'}\n"
            f"⏱