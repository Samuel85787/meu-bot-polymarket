import asyncio
import datetime
import json
import logging
import math
import re
import time
from collections import defaultdict, deque

import websockets

import config
from gamma_api import fetch_active_markets
from price_tracker import PriceTracker
from sports_score_checker import obter_placar_mercado
from telegram_notifier import send_alert
from market_context import formatar_contexto_mercado
from alert_history import record_alert
from signal_quality import classificar_fluxo
from health_status import set_service
from telegram_ui import multiplicador_bruto_contrato

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
        self.order_books = {}
        self.trade_history = defaultdict(deque)

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
        active_tokens = set(token_to_market)
        for token_id in list(self.order_books):
            if token_id not in active_tokens:
                self.order_books.pop(token_id, None)
        for token_id in list(self.trade_history):
            if token_id not in active_tokens:
                self.trade_history.pop(token_id, None)
        self.tracker.remove_tokens(active_tokens)

    def dentro_faixa_preco(self, price):
        if not config.FILTER_PRICE_RANGE:
            return True
        return config.MIN_PRICE <= price <= config.MAX_PRICE

    async def sync_subscriptions(self):
        if not self._ws or not self.connected:
            return
        current_assets = set(self.token_to_market.keys())
        novos = sorted(current_assets - self._subscribed_assets)
        removidos = sorted(self._subscribed_assets - current_assets)
        if novos:
            await self._ws.send(json.dumps({"assets_ids": novos, "operation": "subscribe"}))
            self._subscribed_assets.update(novos)
            logger.info("Novos tokens assinados no CLOB: %s", len(novos))
        if removidos:
            await self._ws.send(json.dumps({"assets_ids": removidos, "operation": "unsubscribe"}))
            self._subscribed_assets.difference_update(removidos)
            for token_id in removidos:
                self.order_books.pop(token_id, None)
                self.trade_history.pop(token_id, None)

    def update_order_book(self, data):
        token_id = str(data.get("asset_id") or "")
        if not token_id:
            return
        def parse_levels(levels, reverse):
            parsed = []
            for level in levels or []:
                try:
                    price = self.parse_float(level.get("price"))
                    size = self.parse_float(level.get("size"))
                    if 0 < price < 1 and size > 0:
                        parsed.append((price, size))
                except (AttributeError, TypeError):
                    continue
            return sorted(parsed, key=lambda item: item[0], reverse=reverse)
        bids = parse_levels(data.get("bids"), True)
        asks = parse_levels(data.get("asks"), False)
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
        self.order_books[token_id] = {
            "best_bid": best_bid, "best_ask": best_ask, "spread": spread,
            "bid_depth_5": sum(size for _, size in bids[:5]),
            "ask_depth_5": sum(size for _, size in asks[:5]),
            "updated_at": time.monotonic(),
            "depth_updated_at": time.monotonic(),
        }

    def update_best_quotes(self, data):
        changes = data.get("price_changes") or [data]
        for change in changes:
            token_id = str(change.get("asset_id") or "")
            book = self.order_books.get(token_id)
            if not book:
                continue
            if "best_bid" in change:
                value = self.parse_float(change.get("best_bid"), None)
                book["best_bid"] = value if value is not None and 0 < value < 1 else None
            if "best_ask" in change:
                value = self.parse_float(change.get("best_ask"), None)
                book["best_ask"] = value if value is not None and 0 < value < 1 else None
            bid, ask = book["best_bid"], book["best_ask"]
            book["spread"] = ask - bid if bid is not None and ask is not None else None
            book["updated_at"] = time.monotonic()

    def formatar_book(self, token_id):
        book = self.order_books.get(str(token_id))
        if not book or time.monotonic() - book["updated_at"] > 120:
            return None

        bid = f"{book['best_bid']:.3f}" if book["best_bid"] is not None else "—"
        ask = f"{book['best_ask']:.3f}" if book["best_ask"] is not None else "—"
        spread = f"{book['spread']:.3f}" if book["spread"] is not None else "—"
        depth_age = time.monotonic() - book.get("depth_updated_at", 0)
        texto = f"Bid {bid}  ·  Ask {ask}  ·  Spread {spread}"
        if depth_age <= 120:
            texto += (
                f"\nProfundidade: {book['bid_depth_5']:.0f} compra / "
                f"{book['ask_depth_5']:.0f} venda tokens"
            )
        else:
            texto += "\nProfundidade: snapshot indisponível"
        return texto

    async def refresh_markets_loop(self):
        while True:
            try:
                await asyncio.sleep(config.MARKET_REFRESH_SECONDS)
                novos_mercados = await fetch_active_markets()
                if novos_mercados is None:
                    logger.warning("Atualizacao Gamma inconclusiva; mantendo mercados atuais")
                    continue
                self.update_markets(novos_mercados)
                set_service("gamma_api", "ok", f"{len(novos_mercados)} mercados carregados")
                await self.sync_subscriptions()
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

    def titulo_evento_curto(self, market):
        """Retorna um nome curto para o evento, sem copiar regras longas do mercado."""
        events = market.get("events") or []
        event = events[0] if events and isinstance(events[0], dict) else {}
        for value in (event.get("title"), event.get("name"), market.get("groupItemTitle")):
            if value and len(str(value).strip()) <= 90:
                return str(value).strip()
        question = str(market.get("question") or "")
        match = re.search(r"between\s+(.+?)\s+and\s+(.+?)(?:,|\.|$)", question, re.IGNORECASE)
        if match:
            return f"{match.group(1).strip()} x {match.group(2).strip()}"
        return question[:87].rstrip() + ("..." if len(question) > 87 else "") or "Evento não identificado"

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
        status = "AO VIVO" if placar.get("live") is True else (
            "ENCERRADO" if placar.get("ended") is True else None
        )
        partes = [p for p in [status, score, period, elapsed] if p]
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
                        open_timeout=20,
                    ) as ws:
                        self._ws = ws
                        assets = sorted(self.token_to_market.keys())
                        sub_msg = {
                            "type": "market",
                            "assets_ids": assets,
                            "initial_dump": True,
                        }
                        if assets:
                            await ws.send(json.dumps(sub_msg))
                        self._subscribed_assets = set(assets)
                        logger.info("Conectado ao CLOB (%s tokens)", len(assets))
                        self.connected = True
                        set_service("clob_ws", "ok", f"{len(assets)} tokens assinados")

                        async for raw in ws:
                            if raw == "PONG":
                                continue
                            try:
                                data = json.loads(raw)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            events = data if isinstance(data, list) else [data]
                            for ev in events:
                                if not isinstance(ev, dict):
                                    continue
                                event_type = ev.get("event_type")
                                if event_type == "book":
                                    self.update_order_book(ev)
                                elif event_type == "price_change":
                                    self.update_best_quotes(ev)
                                elif event_type == "last_trade_price":
                                    await self.handle_trade(ev)

                        logger.warning("Fluxo CLOB terminou; reconectando")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    set_service("clob_ws", "error", type(e).__name__)
                    logger.warning("Falha WebSocket CLOB: %s", type(e).__name__)
                finally:
                    self.connected = False
                    set_service("clob_ws", "error", "desconectado")
                    self._ws = None
                    self._subscribed_assets.clear()
                    # Os snapshots pertencem à conexão anterior; aguardamos novo dump.
                    self.order_books.clear()

                await asyncio.sleep(5)
        finally:
            self.connected = False
            self._ws = None
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

            if not self.dentro_faixa_preco(price):
                return

            result = self.tracker.add_price(token_id, price)
            if result:
                event_type, old_price, new_price, pct = result
                slug = market.get("slug", "unknown")
                if self.should_alert("spike_" + slug + "_" + outcome_name, config.SPIKE_COOLDOWN_SECONDS):
                    await self.enviar_alerta_oscilacao(
                        market, outcome_name, old_price, new_price, pct, event_type, token_id
                    )

            if config.ENABLE_WHALE_ALERTS:
                await self.detectar_baleia(data, market, outcome_name, price)

        except Exception as e:
            logger.error("Erro trade: %s", e)

    async def enviar_alerta_oscilacao(
        self, market, outcome_name, old_price, new_price, pct, event_type="SPIKE_UP", token_id=None
    ):
        try:
            link = self.gerar_link(market)
            liquidez = self.parse_float(market.get("liquidity", 0))
            esporte = market.get("_sport") or market.get("sport") or "Esporte"
            placar_raw = obter_placar_mercado(market)
            placar = self.formatar_placar(placar_raw)
            direcao = "▲ SUBIU" if event_type == "SPIKE_UP" else "▼ CAIU"
            retorno_anterior = multiplicador_bruto_contrato(old_price)
            retorno_atual = multiplicador_bruto_contrato(new_price)

            linhas = [
                f"📈 PREÇO EM MOVIMENTO  ·  {direcao}",
                "━━━━━━━━━━━━━━━━━━━━",
                f"{self.titulo_evento_curto(market)}  ·  {esporte}",
                f"🎯 {outcome_name}",
                f"{old_price:.3f}  →  {new_price:.3f}  ({pct * 100:+.1f}%)",
            ]
            if retorno_anterior and retorno_atual:
                linhas.append(
                    f"Retorno bruto se vencer: {retorno_anterior}  →  {retorno_atual}"
                )
            linhas.append(f"💧 Liquidez do mercado: ${liquidez:,.0f}")
            if placar:
                linhas.append(f"🏟️ {placar}")
            linhas.extend([
                "",
                "Preço do contrato Polymarket; retorno bruto antes de taxas. "
                "Movimentação não é recomendação de entrada.",
            ])
            enviado = await send_alert(
                "\n".join(linhas), button_url=link, button_label="Ver mercado ↗"
            )
            if enviado:
                record_alert(
                    "price_spike", event_type, market, token_id=token_id,
                    outcome=outcome_name, entry_price=new_price,
                    details={"previous_price": old_price, "change_pct": pct * 100},
                )
        except Exception as e:
            logger.error("Erro alerta oscilacao: %s", e)

    async def detectar_baleia(self, data, market, outcome_name, price):
        try:
            size = self.parse_float(data.get("size", 0))
            usd_value = size * price
            liquidez = self.parse_float(market.get("liquidity", 0))

            ratio_liquidez = (usd_value / liquidez) if liquidez > 0 else 0

            side_raw = str(data.get("side", "?")).upper()
            history = self.trade_history[str(data.get("asset_id", ""))]
            now = time.monotonic()
            history.append((now, usd_value, side_raw))
            while history and now - history[0][0] > 1800:
                history.popleft()
            buy_trades = [value for _, value, side in history if side == "BUY"]
            sell_trades = [value for _, value, side in history if side == "SELL"]
            buy_flow = sum(buy_trades)
            sell_flow = sum(sell_trades)
            flow_quality = classificar_fluxo(
                buy_flow=buy_flow,
                sell_flow=sell_flow,
                buy_count=len(buy_trades),
                sell_count=len(sell_trades),
                min_flow_usd=config.MIN_BET_AMOUNT,
                single_trade_usd=usd_value,
                impact_pct=ratio_liquidez * 100,
                min_impact_pct=config.WHALE_LIQUIDITY_RATIO * 100,
            )
            total_flow = flow_quality["total_flow_usd"]
            clustered_flow = flow_quality["recurrent"]

            is_big_bet = usd_value >= config.MIN_BET_AMOUNT
            is_insider_bet = (
                usd_value >= config.MIN_IMPACT_BET_AMOUNT
                and ratio_liquidez >= config.WHALE_LIQUIDITY_RATIO
            )
            if not is_big_bet and not is_insider_bet and not clustered_flow:
                return

            motivos = [flow_quality["label"]]
            if is_big_bet:
                motivos.append("aposta individual acima do limite configurado")
            if is_insider_bet:
                motivos.append(
                    f"valor equivalente a {ratio_liquidez * 100:.1f}% da liquidez"
                )
            motivo = " | ".join(motivos)

            slug = market.get("slug", "unknown")
            alert_key = f"whale_{slug}_{outcome_name}_{side_raw}"
            if not self.should_alert(alert_key, config.WHALE_COOLDOWN_SECONDS):
                return

            placar_raw = await asyncio.to_thread(obter_placar_mercado, market)
            placar = self.formatar_placar(placar_raw)
            fluxo = (
                f"30 min: compras ${buy_flow:,.0f} ({len(buy_trades)} operações) | "
                f"vendas ${sell_flow:,.0f} ({len(sell_trades)} operações) | "
                f"saldo ${flow_quality['net_flow_usd']:+,.0f}"
            )
            book = self.formatar_book(data.get("asset_id", ""))
            await self.enviar_alerta_baleia(
                market, outcome_name, price, side_raw, usd_value, placar, motivo,
                fluxo, book, str(data.get("asset_id", "")),
            )

        except Exception as e:
            logger.error("Erro baleia: %s", e)

    async def enviar_alerta_baleia(
        self, market, outcome_name, price, side_raw, usd_value, placar, motivo,
        fluxo=None, book=None, token_id=None,
    ):
        try:
            liquidez = self.parse_float(market.get("liquidity", 0))
            side_texto = self.side_label(side_raw)
            link = self.gerar_link(market)
            esporte = market.get("_sport") or market.get("sport") or "Esporte"
            titulo = "GRANDE MOVIMENTAÇÃO" if usd_value >= config.MEGA_BET_AMOUNT else "ORDEM DE GRANDE VOLUME"
            retorno = multiplicador_bruto_contrato(price)

            linhas = [
                f"🐋 {titulo}",
                "━━━━━━━━━━━━━━━━━━━━",
                f"{self.titulo_evento_curto(market)}  ·  {esporte}",
                f"{'🟢' if side_raw == 'BUY' else '🔴'} {side_texto}  ·  {outcome_name}",
                f"💵 Ordem estimada: ${usd_value:,.0f}",
                f"Preço do contrato: {price:.3f}"
                + (f"  ·  retorno bruto {retorno}" if retorno else ""),
            ]
            if liquidez > 0:
                impacto = usd_value / liquidez * 100
                linhas.append(f"💧 Liquidez: ${liquidez:,.0f}  ·  impacto estimado {impacto:.1f}%")
            if fluxo:
                linhas.extend(["", "📊 FLUXO  ·  30 MIN", fluxo])
            if book:
                book_linhas = [item.strip() for item in str(book).splitlines() if item.strip()]
                if book_linhas:
                    linhas.extend(["", "📚 LIVRO DE OFERTAS", *book_linhas])
            if placar:
                linhas.append(f"🏟️ {placar}")
            if motivo:
                linhas.extend(["", f"ⓘ {motivo}"])
            linhas.extend([
                "",
                "Preço do contrato Polymarket; retorno bruto antes de taxas. "
                "Ordem grande não comprova informação privilegiada ou resultado futuro.",
            ])

            enviado = await send_alert(
                "\n".join(linhas), button_url=link, button_label="Ver mercado ↗"
            )
            if enviado:
                record_alert(
                    "whale", side_raw, market, token_id=token_id,
                    outcome=outcome_name, entry_price=price, stake_usd=usd_value,
                    details={"reason": motivo, "flow": fluxo},
                )
        except Exception as e:
            logger.error("Erro alerta baleia: %s", e)

