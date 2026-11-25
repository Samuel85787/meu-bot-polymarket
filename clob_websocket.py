import asyncio
import datetime
import json
import logging
import math
import time

import websockets

import config
from gamma_api import fetch_active_markets
from price_tracker import PriceTracker
from sports_score_checker import obter_placar_mercado
from telegram_notifier import send_alert

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketMonitor:
    def __init__(self, markets):
        self.markets = []
        self.token_to_market = {}
        self.token_to_outcome = {}
        self.connected = False
        self._ws = None
        self._subscribed_assets = set()

        self.tracker = PriceTracker(
            threshold=config.PRICE_SPIKE_THRESHOLD,
            window=config.PRICE_SPIKE_WINDOW,
            min_samples=config.MIN_SPIKE_SAMPLES,
        )

        self.last_alert_time = {}
        self.update_markets(markets)

    def update_markets(self, markets):
        self.markets = markets or []
        token_to_market = {}
        token_to_outcome = {}

        for market in self.markets:
            token_ids = market.get("clobTokenIds", [])
            outcomes = market.get("outcomes", [])

            for i, token_id in enumerate(token_ids):
                tid = str(token_id)
                token_to_market[tid] = market
                token_to_outcome[tid] = outcomes[i] if i < len(outcomes) else "Desconhecido"

        self.token_to_market = token_to_market
        self.token_to_outcome = token_to_outcome

    async def subscribe_new_assets(self):
        if not self._ws or not self.connected:
            return
        current_assets = set(self.token_to_market.keys())
        novos = list(current_assets - self._subscribed_assets)
        if novos:
            msg = {
                "type": "market",
                "assets_ids": novos,
                "initial_dump": True,
            }
            await self._ws.send(json.dumps(msg))
            self._subscribed_assets.update(novos)
            logger.info("Novos tokens assinados no CLOB: %s", len(novos))

    async def refresh_markets_loop(self):
        while True:
            try:
                await asyncio.sleep(config.MARKET_REFRESH_SECONDS)
                novos_mercados = await fetch_active_markets()
                if novos_mercados:
                    self.update_markets(novos_mercados)
                    await self.subscribe_new_assets()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Erro ao atualizar mercados: %s", e)

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
                return config.POLYMARKET_BASE_URL + "/event/" + event_slug
        market_slug = market.get("slug")
        if market_slug:
            return config.POLYMARKET_BASE_URL + "/market/" + market_slug
        return config.POLYMARKET_BASE_URL

    def parse_float(self, value, default=0.0):
        try:
            numero = float(value)
            if not math.isfinite(numero):
                return default
            return numero
        except (TypeError, ValueError):
            return default

    def side_label(self, side_raw):
        if str(side_raw).upper() == "BUY":
            return "COMPRA"
        if str(side_raw).upper() == "SELL":
            return "VENDA"
        return "Desconhecido"

    def formatar_placar(self, placar):
        if not placar or not isinstance(placar, dict):
            return None
        score = str(placar.get("score") or "").strip()
        period = str(placar.get("period") or "").strip()
        elapsed = str(placar.get("elapsed") or "").strip()
        if not score and not period:
            return None
        partes = [p for p in [score, period, elapsed] if p]
        return " | ".join(partes)

    async def run(self):
        refresh_task = asyncio.create_task(self.refresh_markets_loop())
        try:
            while True:
                try:
                    async with websockets.connect(
                        WS_URL,
                        ping_interval=20,
                        close_timeout=10,
                    ) as ws:
                        self._ws = ws
                        assets = list(self.token_to_market.keys())
                        sub_msg = {
                            "type": "market",
                            "assets_ids": assets,
                            "initial_dump": True,
                        }
                        await ws.send(json.dumps(sub_msg))
                        self._subscribed_assets = set(assets)
                        logger.info("Conectado ao CLOB (%s tokens)", len(assets))
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
                                if isinstance(ev, dict) and ev.get("event_type") == "last_trade_price":
                                    await self.handle_trade(ev)

                except asyncio.CancelledError:
                    raise
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("Conexao WebSocket fechada")
                    self.connected = False
                    self._ws = None
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error("Erro WebSocket: %s", e)
                    self.connected = False
                    self._ws = None
                    await asyncio.sleep(5)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

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
                slug = market.get("slug", "unknown")
                if self.should_alert("spike_" + slug + "_" + outcome_name, config.SPIKE_COOLDOWN_SECONDS):
                    await self.enviar_alerta_oscilacao(
                        market, outcome_name, old_price, new_price, pct, event_type
                    )

            if config.ENABLE_WHALE_ALERTS:
                await self.detectar_baleia(data, market, outcome_name, price)

        except Exception as e:
            logger.error("Erro trade: %s", e)

    async def enviar_alerta_oscilacao(
        self, market, outcome_name, old_price, new_price, pct, event_type="SPIKE_UP"
    ):
        try:
            link = self.gerar_link(market)
            volume_total = self.parse_float(market.get("volume", 0))
            question = market.get("question", "Mercado desconhecido")
            titulo = "PRECO SUBIU" if event_type == "SPIKE_UP" else "PRECO CAIU"

            msg_text = (
                f"{titulo}\n"
                f"Jogo: {question}\n"
                f"Palpite: {outcome_name}\n"
                f"Preco: {round(old_price, 4)} -> {round(new_price, 4)}\n"
                f"Oscilacao: {round(pct * 100, 2)}%\n"
                f"Volume: ${int(volume_total)}\n"
                f"Link: {link}"
            )

            await send_alert(msg_text)
        except Exception as e:
            logger.error("Erro alerta oscilacao: %s", e)

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

            slug = market.get("slug", "unknown")
            if not self.should_alert("whale_" + slug, config.WHALE_COOLDOWN_SECONDS):
                return

            placar_raw = await asyncio.to_thread(obter_placar_mercado, market)
            placar = self.formatar_placar(placar_raw)

            await self.enviar_alerta_baleia(
                market, outcome_name, price, data.get("side", "?"), usd_value, placar
            )

        except Exception as e:
            logger.error("Erro baleia: %s", e)

    async def enviar_alerta_baleia(self, market, outcome_name, price, side_raw, usd_value, placar):
        try:
            volume_total = self.parse_float(market.get("volume", 0))
            liquidez = self.parse_float(market.get("liquidity", 0))
            side_texto = self.side_label(side_raw)
            link = self.gerar_link(market)
            question = market.get("question", "Mercado desconhecido")

            titulo = "MEGA BALEIA" if usd_value >= config.MEGA_BET_AMOUNT else "Aposta Grande"

            msg_text = (
                f"{titulo}\n"
                f"Jogo: {question}\n"
                f"Palpite: {outcome_name}\n"
                f"Acao: {side_texto}\n"
                f"Valor: ${round(usd_value, 2)}\n"
                f"Preco: {round(price, 4)}\n"
                f"Volume: ${int(volume_total)}\n"
                f"Liquidez: ${int(liquidez)}\n"
            )
            if placar:
                msg_text += f"Placar: {placar}\n"
            msg_text += f"Link: {link}"

            await send_alert(msg_text)
        except Exception as e:
            logger.error("Erro alerta baleia: %s", e)
