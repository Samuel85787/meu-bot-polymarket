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
            if extra:
                status = f"Ao vivo ({extra})"
            else:
                status = "Ao vivo"
        else:
            status = "Pré-jogo"

        bloco = (
            "━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Placar:</b> {score or 'N/D'}\n"
            f"⏱️ <b>Status:</b> {status}\n"
        )

        return bloco

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
                    logger.info(f"🔗 Conectado. Monitorando {len(assets)} tokens.")
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
                logger.warning("⚠️ Conexão WebSocket fechada. Reconectando em 5s...")
                self.connected = False
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"⚠️ Erro na conexão: {type(e).__name__}: {e}. Reconectando em 5s...")
                self.connected = False
                await asyncio.sleep(5)

    async def handle_trade(self, data):
        try:
            token_id = str(data.get("asset_id", ""))
            price = self.parse_float(data.get("price", 0))

            if token_id not in self.token_to_market:
                return

            market = self.token_to_market[token_id]
            outcome_name = html.escape(self.token_to_outcome.get(token_id, "Desconhecido"))

            if self.is_market_finished(market):
                return

            if price <= 0 or price >= 1:
                return

            # Detecta oscilação de preço
            result = self.tracker.add_price(token_id, price)
            if result:
                event_type, old_price, new_price, pct = result

                if pct > 0:
                    if self.should_alert(f"spike_{market['slug']}", config.SPIKE_COOLDOWN_SECONDS):
                        await self.enviar_alerta_oscilacao(market, outcome_name, old_price, new_price, pct)

            # Detecta baleias
            if config.ENABLE_WHALE_ALERTS:
                await self.detectar_baleia(data, market, outcome_name, price)

        except Exception as e:
            logger.error(f"Erro ao processar trade: {type(e).__name__}: {e}")

    async def enviar_alerta_oscilacao(self, market, outcome_name, old_price, new_price, pct):
        try:
            link = self.gerar_link(market)
            volume_total = self.parse_float(market.get("volume", 0))
            liquidez = self.parse_float(market.get("liquidity", 0))
            market_type, odd_label = self.market_type_label(market)

            question = html.escape(market.get("question", "Mercado desconhecido"))

            msg = (
                "📈 <b>PREÇO SUBIU</b> (Odd Caiu)\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"🏆 <b>Jogo:</b> {question}\n"
                f"🎯 <b>Palpite:</b> <code>{outcome_name}</code>\n"
                f"📌 <b>Tipo:</b> {market_type}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Preço:</b> {old_price:.4f} → {new_price:.4f}\n"
                f"💰 <b>{odd_label}:</b> {1/old_price:.2f} → {1/new_price:.2f}\n"
                f"📈 <b>Oscilação:</b> +{pct*100:.2f}%\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Volume do Mercado:</b> ${volume_total:,.0f}\n"
                f"💧 <b>Liquidez:</b> ${liquidez:,.0f}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "💡 <i>O mercado está confiante. Alguém sabe de algo.</i>\n\n"
                f"<a href=\"{link}\">👉 ABRIR NA POLYMARKET</a>"
            )
            await send_alert(msg)
        except Exception as e:
            logger.error(f"Erro ao enviar alerta de oscilação: {type(e).__name__}: {e}")

    async def detectar_baleia(self, data, market, outcome_name, price):
        try:
            size = self.parse_float(data.get("size", 0))
            usd_value = size * price
            liquidez = self.parse_float(market.get("liquidity", 0))

            ratio_liquidez = (usd_value / liquidez) if liquidez > 0 else 0

            is_big_bet = usd_value >= config.MIN_BET_AMOUNT
            is_insider_bet = ratio_liquidez >= config.WHALE_LIQUIDITY_RATIO

            if not is_big_bet and not is_insider_bet:
                return

            agora = time.time()
            if agora - self.last_whale_alert_global < config.WHALE_COOLDOWN_SECONDS:
                return

            if not self.should_alert(f"whale_{market['slug']}", config.WHALE_COOLDOWN_SECONDS):
                return

            self.last_whale_alert_global = agora

            placar = obter_placar_mercado(market)
            await self.enviar_alerta_baleia(market, outcome_name, price, data.get("side", "?"), usd_value, ratio_liquidez, placar)

        except Exception as e:
            logger.error(f"Erro ao detectar baleia: {type(e).__name__}: {e}")

    async def enviar_alerta_baleia(self, market, outcome_name, price, side_raw, usd_value, ratio_liquidez, placar):
        try:
            volume_total = self.parse_float(market.get("volume", 0))
            liquidez = self.parse_float(market.get("liquidity", 0))
            market_type, odd_label = self.market_type_label(market)
            side_texto = self.side_label(side_raw)
            link = self.gerar_link(market)

            question = html.escape(market.get("question", "Mercado desconhecido"))
            outcome_escaped = html.escape(outcome_name)

            # Determina tipo de alerta
            if ratio_liquidez >= config.WHALE_LIQUIDITY_RATIO:
                titulo = "🎯🔥 <b>ALERTA DE GRANDE VOLUME</b> 🔥🎯"
                leitura = "⚠️ <i>Negociação grande em mercado de baixa liquidez.</i>"
            elif usd_value >= config.MEGA_BET_AMOUNT:
                titulo = "🚨🔥 <b>MEGA BALEIA NA ÁREA!</b> 🔥🚨"
                leitura = "⚠️ <i>Volume muito acima do normal.</i>"
            else:
                titulo = "🐋 <b>Aposta Grande Detectada</b>"
                leitura = "💡 <i>Dinheiro inteligente entrando no mercado.</i>"

            bloco_placar = self.formatar_placar(placar)

            msg = (
                f"{titulo}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"🏆 <b>Jogo:</b> {question}\n"
                f"🎯 <b>Palpite:</b> <code>{outcome_escaped}</code>\n"<span class="ml-2" /><span data-testid="markdown-streaming-circle" class="inline-block w-3 h-3 rounded-full bg-neutral-a12 align-middle mb-[0.1rem]" />