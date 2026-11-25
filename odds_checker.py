import asyncio
import logging
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone

import aiohttp
import config
from market_context import formatar_contexto_mercado
from telegram_notifier import send_alert

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SUPPORTED_SPORTS = ("NBA", "NFL", "Tênis ATP", "Tênis WTA", "CS2", "VALORANT")
SPORT_CACHE_SECONDS = 21600
_sports_cache = []
_sports_cache_at = 0.0
_last_odds_alert = {}


def normalize_team_name(name):
    value = unicodedata.normalize("NFKD", str(name or "").lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _sport_for_key(sport):
    key = normalize_team_name(sport.get("key", ""))
    title = normalize_team_name(sport.get("title", ""))
    text = f"{key} {title}"

    if key == "basketball nba" or "nba" in text:
        return "NBA"
    if key == "americanfootball nfl" or "nfl" in text:
        return "NFL"
    if "tennis" in text and "atp" in text:
        return "Tênis ATP"
    if "tennis" in text and "wta" in text:
        return "Tênis WTA"
    if any(term in text for term in ("counter strike", "counterstrike", "csgo", "cs2")):
        return "CS2"
    if "valorant" in text:
        return "VALORANT"
    return None


async def discover_supported_sports(session):
    """Descobre os sport keys efetivamente disponibilizados pela conta da Odds API."""
    global _sports_cache, _sports_cache_at
    now = time.monotonic()
    if _sports_cache and now - _sports_cache_at < SPORT_CACHE_SECONDS:
        return list(_sports_cache)

    url = f"{ODDS_API_BASE}/sports/"
    params = {"apiKey": config.ODDS_API_KEY, "all": "true"}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning("Odds API sports discovery: HTTP %s", resp.status)
                return list(_sports_cache)
            payload = await resp.json()
            if not isinstance(payload, list):
                return list(_sports_cache)

        discovered = {}
        for item in payload:
            if not isinstance(item, dict) or not item.get("active", True):
                continue
            category = _sport_for_key(item)
            key = str(item.get("key") or "").strip()
            if category in SUPPORTED_SPORTS and key:
                discovered.setdefault(category, []).append(key)

        _sports_cache = [
            (key, category)
            for category in SUPPORTED_SPORTS
            for key in discovered.get(category, [])
        ]
        _sports_cache_at = now
        for category in SUPPORTED_SPORTS:
            if not discovered.get(category):
                logger.info("Odds API sem feed ativo identificado para %s", category)
        return list(_sports_cache)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Falha ao descobrir esportes na Odds API: %s", type(exc).__name__)
        return list(_sports_cache)


def _market_text(pm_market):
    events = pm_market.get("events") or []
    event_text = " ".join(
        str(event.get(field) or "")
        for event in events if isinstance(event, dict)
        for field in ("title", "name", "slug")
    )
    return " ".join(
        str(value or "")
        for value in (
            pm_market.get("question"), pm_market.get("slug"),
            pm_market.get("description"), event_text,
        )
    )


def _contains_team(text, team):
    normalized_text = f" {normalize_team_name(text)} "
    normalized_team = normalize_team_name(team)
    return bool(normalized_team and f" {normalized_team} " in normalized_text)


def _parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _market_start_time(pm_market):
    events = pm_market.get("events") or []
    candidates = [
        pm_market.get("startDate"), pm_market.get("startTime"),
        pm_market.get("gameStartTime"),
    ]
    for event in events:
        if isinstance(event, dict):
            candidates.extend((event.get("startDate"), event.get("startTime"), event.get("gameStartTime")))
    for candidate in candidates:
        parsed = _parse_datetime(candidate)
        if parsed:
            return parsed
    return None


def teams_match(home_team, away_team, question, pm_market=None, commence_time=None):
    """Só associa partidas com evidência textual e, quando disponível, temporal."""
    market = pm_market or {"question": question}
    text = _market_text(market)
    home_found = _contains_team(text, home_team)
    away_found = _contains_team(text, away_team)
    if not (home_found and away_found):
        # Mercados binários podem citar apenas o participante selecionado.
        # Nesse caso, exige também evidência temporal para evitar associação
        # por coincidência de nomes.
        if not (home_found or away_found):
            return False
        outcomes = {normalize_team_name(value) for value in market.get("outcomes", [])}
        if not outcomes.intersection({"yes", "no"}):
            return False

    market_time = _market_start_time(market)
    game_time = _parse_datetime(commence_time)
    if not market_time or not game_time:
        return False

    # Jogos no mesmo dia UTC podem ter horários de início diferentes entre
    # provedores. Para datas diferentes, só tolera uma virada de dia próxima.
    if market_time.date() == game_time.date():
        return True
    return abs((market_time - game_time).total_seconds()) <= 6 * 60 * 60


def _resolve_side(outcome_name, home_team, away_team):
    outcome = normalize_team_name(outcome_name)
    home = normalize_team_name(home_team)
    away = normalize_team_name(away_team)
    if outcome == home or (home and f" {home} " in f" {outcome} "):
        return "home"
    if outcome == away or (away and f" {away} " in f" {outcome} "):
        return "away"
    return None


def _infer_yes_side(pm_market, home_team, away_team):
    text = str(pm_market.get("question") or "")
    home_found = _contains_team(text, home_team)
    away_found = _contains_team(text, away_team)
    if home_found != away_found:
        return "home" if home_found else "away"

    normalized = normalize_team_name(text)
    # Em perguntas binárias com ambos os participantes, o primeiro nome após
    # "will" normalmente é a seleção afirmativa. Se não for inequívoco, não aposta.
    match = re.search(r"\bwill\s+(.+?)\s+(?:win|beat|defeat)\b", normalized)
    if match:
        subject = match.group(1)
        if normalize_team_name(home_team) in subject:
            return "home"
        if normalize_team_name(away_team) in subject:
            return "away"
    return None


def calcular_probabilidades_sem_vig(outcomes):
    """Normaliza probabilidades implícitas, removendo a margem da casa."""
    implicadas = {}
    soma_inversa = 0.0
    for item in outcomes or []:
        nome = str(item.get("name", "")).strip()
        try:
            odd = float(item.get("price", 0))
        except (TypeError, ValueError):
            continue
        if not nome or not math.isfinite(odd) or odd <= 1.0:
            continue
        probabilidade = 1.0 / odd
        implicadas[normalize_team_name(nome)] = (nome, odd, probabilidade)
        soma_inversa += probabilidade

    if soma_inversa <= 0:
        return {}
    return {
        chave: {
            "name": nome,
            "odds": odd,
            "raw_prob": probabilidade,
            "fair_prob": probabilidade / soma_inversa,
        }
        for chave, (nome, odd, probabilidade) in implicadas.items()
    }


async def check_arbitrage(pm_markets=None):
    if not config.ODDS_API_KEY:
        logger.warning("ODDS_API_KEY nao configurada")
        return

    try:
        if pm_markets is None:
            from gamma_api import fetch_active_markets
            pm_markets = await fetch_active_markets()
    except Exception as exc:
        logger.error("Erro ao buscar mercados Polymarket: %s", type(exc).__name__)
        return

    timeout = aiohttp.ClientTimeout(total=25, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        sports = await discover_supported_sports(session)
        if not sports:
            logger.warning("Nenhum esporte suportado disponível na Odds API nesta conta")
            return
        for sport_key, sport_name in sports:
            try:
                await check_sport_arbitrage(session, sport_key, sport_name, pm_markets or [])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Erro em %s: %s", sport_name, type(exc).__name__)
            await asyncio.sleep(1)


async def check_sport_arbitrage(session, sport_key, sport_name, pm_markets):
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": "eu",
        "bookmakers": "pinnacle",
        "markets": "h2h",
    }
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.warning("Odds API %s: HTTP %s", sport_name, resp.status)
                return
            odds_data = await resp.json()
            if not isinstance(odds_data, list):
                logger.warning("Resposta inesperada da Odds API para %s", sport_name)
                return
            for game in odds_data:
                if isinstance(game, dict) and game.get("bookmakers"):
                    await process_game(game, pm_markets, sport_name, session)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Falha ao consultar %s: %s", sport_name, type(exc).__name__)


async def process_game(game, pm_markets, sport_name, session=None):
    try:
        pinnacle = next(
            (book for book in game.get("bookmakers", [])
             if str(book.get("key", "")).lower() == "pinnacle"),
            None,
        )
        if not pinnacle:
            return
        pinnacle_market = next(
            (item for item in pinnacle.get("markets", []) if item.get("key") == "h2h"),
            None,
        )
        if not pinnacle_market:
            return

        home_team = str(game.get("home_team") or "")
        away_team = str(game.get("away_team") or "")
        if not home_team or not away_team:
            return

        for pm_market in pm_markets:
            if pm_market.get("_sport") != sport_name:
                continue
            if str(pm_market.get("sportsMarketType", "")).lower() != "moneyline":
                continue
            if not teams_match(
                home_team, away_team, pm_market.get("question", ""),
                pm_market, game.get("commence_time"),
            ):
                continue
            await check_outcomes(
                game, pinnacle_market, pm_market, home_team, away_team, session
            )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Erro ao processar partida: %s", type(exc).__name__)


def find_pm_token_id(pm_market, target_side, yes_side, home_team, away_team):
    outcomes, tokens = pm_market.get("outcomes", []), pm_market.get("clobTokenIds", [])
    for index, outcome in enumerate(outcomes):
        if index < len(tokens) and _resolve_side(outcome, home_team, away_team) == target_side:
            return str(tokens[index])
    normalized = [normalize_team_name(value) for value in outcomes]
    if "yes" in normalized and "no" in normalized and yes_side:
        index = normalized.index("yes" if target_side == yes_side else "no")
        if index < len(tokens):
            return str(tokens[index])
    return None


def _fee_schedule(pm_market):
    schedule = pm_market.get("feeSchedule") or pm_market.get("fee_schedule")
    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except (TypeError, ValueError):
            return None
    if isinstance(schedule, dict):
        try:
            rate = float(schedule.get("rate"))
            exponent = float(schedule.get("exponent", 1))
        except (TypeError, ValueError):
            return None
        if math.isfinite(rate) and math.isfinite(exponent) and rate >= 0 and exponent >= 0:
            return {"rate": rate, "exponent": exponent}
        return None
    if pm_market.get("feesEnabled") is False or str(pm_market.get("feesEnabled", "")).lower() == "false":
        return {"rate": 0.0, "exponent": 1.0}
    return None


def calcular_ev_liquido(fair_prob, book, fee_schedule, stake_usd):
    if not 0 < fair_prob < 1 or stake_usd <= 0 or not fee_schedule:
        return None
    rate, exponent = fee_schedule["rate"], fee_schedule["exponent"]
    remaining = float(stake_usd)
    cost = gross_shares = fee_usd = fee_shares = 0.0
    best_ask = best_bid = None
    for level in book.get("asks", []):
        try:
            price, size = float(level["price"]), float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < price < 1) or not math.isfinite(size) or size <= 0:
            continue
        if best_ask is None:
            best_ask = price
        fill_cost = min(remaining, price * size)
        shares = fill_cost / price
        cost += fill_cost
        gross_shares += shares
        level_fee_usd = shares * rate * (price * (1 - price)) ** exponent
        fee_usd += level_fee_usd
        fee_shares += level_fee_usd / price
        remaining -= fill_cost
        if remaining <= 1e-8:
            break
    for level in book.get("bids", []):
        try:
            price, size = float(level["price"]), float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < price < 1 and size > 0 and math.isfinite(size):
            best_bid = max(best_bid or 0, price)
    if remaining > 1e-6 or gross_shares <= 0 or cost <= 0:
        return None
    average_price = cost / gross_shares
    net_shares = gross_shares - fee_shares
    net_profit = fair_prob * net_shares - cost
    spread_pct = (((best_ask - best_bid) / ((best_ask + best_bid) / 2)) * 100
                  if best_ask is not None and best_bid is not None and best_ask > best_bid else 0.0)
    slippage_pct = ((average_price / best_ask) - 1) * 100 if best_ask else 0.0
    return {
        "stake_usd": cost, "average_price": average_price, "best_ask": best_ask,
        "best_bid": best_bid, "spread_pct": spread_pct, "slippage_pct": slippage_pct,
        "fee_usd": fee_usd, "gross_shares": gross_shares, "net_shares": net_shares,
        "net_profit_usd": net_profit, "net_ev_pct": net_profit / cost * 100,
    }


async def _estimate_market_ev(session, token_id, fair_prob, pm_market, stake_usd):
    schedule = _fee_schedule(pm_market)
    if schedule is None:
        logger.info("FeeSchedule indisponivel; EV ignorado para mercado %s", pm_market.get("id"))
        return None
    try:
        async with session.get(config.CLOB_BASE_URL + "/book", params={"token_id": token_id}) as resp:
            if resp.status != 200:
                return None
            book = await resp.json()
        if not isinstance(book, dict):
            return None
        return calcular_ev_liquido(fair_prob, book, schedule, stake_usd)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        logger.warning("Falha ao calcular EV no CLOB: %s", type(exc).__name__)
        return None


async def check_outcomes(game, pinnacle_market, pm_market, home_team, away_team, session=None):
    fair_map = calcular_probabilidades_sem_vig(pinnacle_market.get("outcomes", []))
    if not fair_map:
        return

    yes_side = _infer_yes_side(pm_market, home_team, away_team)
    for info in fair_map.values():
        side = _resolve_side(info["name"], home_team, away_team)
        if side is None:
            continue
        token_id = find_pm_token_id(pm_market, side, yes_side, home_team, away_team)
        if not token_id or session is None:
            continue
        fair_prob = info["fair_prob"]
        ev_data = await _estimate_market_ev(
            session, token_id, fair_prob, pm_market, config.ODDS_ESTIMATED_STAKE_USD
        )
        if ev_data is None or ev_data["net_ev_pct"] < config.ODDS_EDGE_THRESHOLD * 100:
            continue
        key = f"{game.get('id', '')}:{normalize_team_name(info['name'])}:{pm_market.get('conditionId', pm_market.get('id', ''))}"
        now = time.monotonic()
        last_alert = _last_odds_alert.get(key)
        if last_alert is not None and now - last_alert < config.ODDS_COOLDOWN_SECONDS:
            continue
        enviado = await send_arbitrage_alert(
            game, info, info["odds"], fair_prob, ev_data, pm_market
        )
        if enviado:
            _last_odds_alert[key] = now


def find_pm_price(pm_market, target_side, yes_side, home_team, away_team):
    outcomes = pm_market.get("outcomes", [])
    prices = pm_market.get("outcomePrices", [])
    for index, outcome in enumerate(outcomes):
        if index >= len(prices):
            continue
        side = _resolve_side(outcome, home_team, away_team)
        if side == target_side:
            try:
                price = float(prices[index])
                return price if math.isfinite(price) else None
            except (TypeError, ValueError):
                return None

    normalized_outcomes = [normalize_team_name(value) for value in outcomes]
    if "yes" in normalized_outcomes and "no" in normalized_outcomes and yes_side:
        yes_index = normalized_outcomes.index("yes")
        no_index = normalized_outcomes.index("no")
        selected_index = yes_index if target_side == yes_side else no_index
        try:
            price = float(prices[selected_index])
            return price if math.isfinite(price) else None
        except (IndexError, TypeError, ValueError):
            return None
    return None


async def send_arbitrage_alert(game, info, pinnacle_odds, fair_prob, ev_data, pm_market):
    try:
        slug = pm_market.get("slug")
        link = (
            config.POLYMARKET_BASE_URL + "/event/" + slug
            if slug else config.POLYMARKET_BASE_URL
        )
        message = (
            "ODDS VALUE - EV LIQUIDO\n"
            f"Evento: {game.get('home_team', '')} vs {game.get('away_team', '')}\n"
            f"{formatar_contexto_mercado(pm_market)}\n"
            f"Seleção: {info['name']}\n"
            f"Pinnacle sem margem: {fair_prob * 100:.1f}% (odd {pinnacle_odds:.2f})\n"
            f"Compra estimada: ${ev_data['stake_usd']:.2f} a ${ev_data['average_price']:.4f} por share\n"
            f"Melhor bid/ask: {ev_data['best_bid'] if ev_data['best_bid'] is not None else '—'} / {ev_data['best_ask']:.4f}\n"
            f"Spread: {ev_data['spread_pct']:.2f}% | Slippage: {ev_data['slippage_pct']:.2f}%\n"
            f"Taxa estimada: ${ev_data['fee_usd']:.4f}\n"
            f"EV líquido: {ev_data['net_ev_pct']:+.2f}% (${ev_data['net_profit_usd']:+.2f})\n"
            f"Link: {link}"
        )
        enviado = await send_alert(message)
        if enviado:
            logger.info("Oportunidade Odds Value enviada: %s", info["name"])
        else:
            logger.warning("Telegram não confirmou o alerta Odds Value: %s", info["name"])
        return bool(enviado)
    except Exception as exc:
        logger.error("Erro ao enviar alerta Odds Value: %s", type(exc).__name__)
        return False
