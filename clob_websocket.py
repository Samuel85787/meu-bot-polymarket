import asyncio
import json
import time
import datetime
import websockets
import logging
from typing import Optional, Tuple, Dict

import config
from telegram_notifier import send_alert
from price_tracker import PriceTracker
from sports_score_checker import obter_placar

logger = logging.getLogger(__name__)

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

    def is_market_finished(self, market: dict) -> bool:
        """Verifica se o mercado já finalizou"""
        end_date_str = market.get("endDate")
        if not end_date_str:
            return False
        try:
            end_date_str = end_date_str.replace("Z", "+00:00")
            end_date = datetime.datetime.fromisoformat(end_date_str)
            end_date_with_buffer = end_date + datetime.timedelta(hours=3)
            now = datetime.datetime.now(datetime.timezone.utc)
            return now > end_date_with_buffer
        except Exception as e:
            logger.warning(f"Erro ao verificar data do mercado: {e}")
            return False

    def gerar_link(self, market: dict) -> str:
        """Gera link para o mercado na Polymarket"""
        if "events" in market and len(market["events"]) > 0:
            event_slug = market["events"][0].get("slug")
            if event_slug:
                return f"{config.POLYMARKET_BASE_URL}/event/{event_slug}"
        market_slug = market.get("slug")
        if market_slug:
            return f"{config.POLYMARKET_BASE_URL}/market/{market_slug}"
        return config.POLYMARKET_BASE_URL

    def should_alert(self, key: str, cooldown_minutes: int = 3) -> bool:
        """Verifica se deve enviar alerta baseado em cooldown"""
        agora = time.time()
        ultimo_alerta = self.last_alert_time.get(key, 0)
        if agora - ultimo_alerta < (cooldown_minutes * 60):
            return False
        self.last_alert_time[key] = agora
        return True

    async def run(self):
        """Loop principal de monitoramento"""
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
                    logger.info(f"🔗 Conectado. Monitorando {len(assets)} tokens.")

                    async for raw in ws:
                        if raw == "PONG":
                            continue
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("JSON inválido recebido")
                            continue

                        events = data if isinstance(data, list) else [data]
                        for ev in events:
                            et = ev.get("event_type")
                            if et == "last_trade_price":
                                await self.handle_trade(ev)
            
            except websockets.exceptions.ConnectionClosed:
                logger.warning("⚠️ Conexão WebSocket fechada. Reconectando em 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"⚠️ Erro na conexão: {type(e).__name__}: {e}. Reconectando em 5s...")
                await asyncio.sleep(5)

    async def handle_trade(self, data: dict):
        """Processa evento de trade"""
        try:
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
            # 1. DETECÇÃO DE OSCILAÇÃO DE PREÇO
            # ==========================================
            await self.detectar_oscilacao_preco(market, token_id, price, outcome_name)

            # ==========================================
            # 2. DETECÇÃO DE BALEIA / INSIDER
            # ==========================================
            if config.ENABLE_WHALE_ALERTS:
                await self.detectar_baleia(data, market, outcome_name, price)

        except Exception as e:
            logger.error(f"Erro ao processar trade: {type(e).__name__}: {e}")

    async def detectar_oscilacao_preco(self, market: dict, token_id: str, 
                                      price: float, outcome_name: str):
        """Detecta e alerta sobre oscilações de preço"""
        result = self.tracker.add_price(token_id, price)
        if result:
            event_type, old_price, new_price, pct = result
            
            if pct > 0:  # Preço subiu
                if self.should_alert(f"spike_{market['slug']}", cooldown_minutes=3):
                    await self.enviar_alerta_oscilacao(market, outcome_name, old_price, new_price, pct)

    async def enviar_alerta_oscilacao(self, market: dict, outcome_name: str,
                                     old_price: float, new_price: float, pct: float):
        """Envia alerta de oscilação de preço"""
        link = self.gerar_link(market)
        volume_total = float(market.get("volume", 0) or 0)
        liquidez = float(market.get("liquidity", 0) or 0)
        market_type = market.get("sportsMarketType", "Desconhecido").capitalize()
        odd_label = "Odd de Vitória" if market_type == "Moneyline" else f"Odd ({market_type})"

        titulo = "📈 *PREÇO SUBIU* (Odd Caiu)"
        leitura = "💡 _O mercado está confiante. Alguém sabe de algo._"
        movimento = f"🔼 `{old_price:.2f} ➔ {new_price:.2f}`"
        odds = f"📉 `{1/old_price:.2f} ➔ {1/new_price:.2f}`"
            
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

    async def detectar_baleia(self, data: dict, market: dict, outcome_name: str, price: float):
        """Detecta e alerta sobre apostas de baleias"""
        try:
            size = float(data.get("size", 0) or 0)
            usd_value = size * price
            liquidez = float(market.get("liquidity", 0) or 0)
            
            ratio_liquidez = (usd_value / liquidez) if liquidez > 0 else 0

            is_big_bet = usd_value >= config.MIN_BET_AMOUNT
            is_insider_bet = ratio_liquidez >= config.WHALE_LIQUIDITY_RATIO

            if not is_big_bet and not is_insider_bet:
                return

            agora = time.time()
            if agora - self.last_whale_alert_global < 600:  # Cooldown global
                return

            if not self.should_alert(f"whale_{market['slug']}", cooldown_minutes=15):
                return

            self.last_whale_alert_global = agora

            # Obtém placar do jogo
            game_id = market.get("gameId") or market.get("sportsMarketType")
            placar = obter_placar(game_id) if game_id else None

            # Determina tipo de alerta
            titulo, leitura = self.determinar_tipo_alerta(is_insider_bet, usd_value, ratio_liquidez, placar)

            await self.enviar_alerta_baleia(
                market, outcome_name, price, data.get("side", "?"),
                usd_value, titulo, leitura, placar
            )

        except Exception as e:
            logger.error(f"Erro ao detectar baleia: {type(e).__name__}: {e}")

    def determinar_tipo_alerta(self, is_insider_bet: bool, usd_value: float,
                              ratio_liquidez: float, placar: Optional[dict]) -> Tuple[str, str]:
        """Determina o tipo de alerta baseado nas condições"""
        if is_insider_bet:
            titulo = "🎯🔥 *ALERTA DE INSIDER (Baixa Liquidez)!* 🔥🎯"
            leitura = "⚠️ _A aposta engoliu parte gigante do mercado. MUITO provável que seja informação interna._"
        elif usd_value >= config.MEGA_BET_AMOUNT:
            titulo = "🚨🔥 *MEGA BALEIA NA ÁREA!* 🔥🚨"
            leitura = "⚠️ _Atenção: Volume fora do comum. Pode ser insider._"
        else:
            titulo = "🐋 *Aposta Grande Detectada*"
            leitura = "💡 _Dinheiro inteligente entrando no mercado._"

        # Se há placar e o time está perdendo muito, pode ser desespero
        if placar and placar.get("score"):
            try:
                numeros = [int(s) for s in placar["score"].replace("-", " ").split() if s.isdigit()]
                if len(numeros) >= 2 and numeros[0] > numeros[1] * 1.5:
                    titulo = "⚠️ *APOSTA EM RECUPERAÇÃO (Risco Alto)*"
                    leitura = "💡 _O time está perdendo por uma diferença grande. A aposta pode ser desespero, não informação._"
            except Exception:
                pass

        return titulo, leitura

    def formatar_bloco_placar(self, placar: Optional[dict]) -> str:
        """Formata bloco de informações do placar"""
        if not placar:
            return ""

        score = placar.get("score", "")
        period = placar.get("period", "")
        live = placar.get("live", False)
        ended = placar.get("ended", False)

        if ended:
            status_texto = "Encerrado"
        elif live:
            status_texto = f"Ao Vivo ({period})" if period else "Ao Vivo"
        else:
            status_texto = "Pré-jogo"

        return (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Placar:* {score}\n"
            f"⏱️ *Status:* {status_texto}\n"
        )

    async def enviar_alerta_baleia(self, market: dict, outcome_name: str, price: float,
                                  side_raw: str, usd_value: float, titulo: str,
                                  leitura: str, placar: Optional[dict]):
        """Envia alerta de baleia"""
        try:
            volume_total = float(market.get("volume", 0) or 0)
            liquidez = float(market.get("liquidity", 0) or 0)
            market_type = market.get("sportsMarketType", "Desconhecido").capitalize()
            odd_label = "Odd de Vitória" if market_type == "Moneyline" else f"Odd ({market_type})"
            side_texto = "🟢 COMPRA (A Favor)" if side_raw == "BUY" else "🔴 VENDA (Contra)"
            link = self.gerar_link(market)

            bloco_placar = self.formatar_bloco_placar(placar)

            msg = (
                f"{titulo}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏆 *Jogo:* {market['question']}\n"
                f"🎯 *Palpite:* `{outcome_name}`\n"
                f"📌 *Tipo:* {market_type}\n"
                f"🔄 *Ação:* {side_texto}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💎 *${usd_value:,.2f}*\n"
                f"📊 *Preço:* {price:.2f} | *{odd_label}:* {1/price:.2f}\n"
                f"{bloco_placar}"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Volume do Mercado:* ${volume_total:,.0f}\n"
                f"💧 *Liquidez Total:* ${liquidez:,.0f}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{leitura}\n\n"
                f"[👉 ABRIR NA POLYMARKET]({link})"
            )
            await send_alert(msg)
            logger.info(f"🐋 Alerta de baleia enviado para {market['question']}")

        except Exception as e:
            logger.error(f"Erro ao enviar alerta de baleia: {type(e).__name__}: {e}")