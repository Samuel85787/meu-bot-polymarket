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
from alert_history import record_alert
from health_status import set_service
from signal_quality import classificar_qualidade_odds

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SUPPORTED_SPORTS = ("NBA", "NFL", "Tênis ATP", "Tênis WTA", "CS2", "VALORANT")
SPORT_CACHE_SECONDS = 21600
_sports_cache = []
_sports_cache_at = 0.0
_sport_rotation = {}
_last_odds_alert = {}
_quota_used = None
_quota_remaining = None
_quota_last = None
_quota_checked_at = None


def atualizar_quota(headers):
    """Lê os cabeçalhos oficiais de consumo da The Odds API."""
    global _quota_used, _quota_remaining, _quota_last, _quota_checked_at
    try:
        used = headers.get("x-requests-used")
        remaining = headers.get("x-requests-remaining")
        last = headers.get("x-requests-last")
        if used is not None:
            _quota_used = max(0, int(used))
        if remaining is not None:
            _quota_remaining = max(0, int(remaining))
        if last is not None:
            _quota_last = max(0, int(last))
        if used is not None or remaining is not None or last is not None:
            _quota_checked_at = time.time()
            reserve = config.ODDS_API_CREDITS_RESERVE
            estado = "warning" if _quota_remaining is not None and _quota_remaining <= reserve else "ok"
            resumo = (
                f"{_quota_used if _quota_used is not None else '—'} usados | "
                f"{_quota_remaining if _quota_remaining is not None else '—'} restantes"
            )
            set_service("odds_api_quota", estado, resumo)
    except (TypeError, ValueError, AttributeError):
        logger.debug("Cabeçalhos de quota da Odds API ausentes ou inválidos")


def get_quota_status():
    total = (
        _quota_used + _quota_remaining
        if _quota_used is not None and _quota_remaining is not None
        else None
    )
    paused = (
        _quota_remaining is not None
        and _quota_remaining <= config.ODDS_API_CREDITS_RESERVE
    )
    return {
        "used": _quota_used,
        "remaining": _quota_remaining,
        "last_cost": _quota_last,
        "total": total,
        "checked_at": _quota_checked_at,
        "reserve": config.ODDS_API_CREDITS_RESERVE,
        "paused": paused,
    }


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

    # /sports é gratuito na The Odds API; consultá-lo em cada ciclo atualiza o saldo.
    url = f"{ODDS_API_BASE}/sports/"
    params = {"apiKey": config.ODDS_API_KEY, "all": "true"}
    try:
        async with session.get(url, params=params) as resp:
            atualizar_quota(resp.headers)
            if resp.status != 200:
                set_service("odds_api", "error", f"sports HTTP {resp.status}")
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
        set_service("odds_api", "ok", f"{len(_sports_cache)} feeds esportivos disponíveis")
        for category in SUPPORTED_SPORTS:
            if not discovered.get(category):
                logger.info("Odds API sem feed ativo identificado para %s", category)
        return list(_sports_cache)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        set_service("odds_api", "error", type(exc).__name__)
        logger.warning("Falha ao descobrir esportes na Odds API: %s", type(exc).__name__)
        return list(_sports_cache)



def selecionar_feeds_para_ciclo(sports, max_feeds=None):
    """Seleciona no máximo um feed por modalidade e alterna torneios entre ciclos."""
    limite = max(1, int(max_feeds or config.ODDS_API_MAX_FEEDS_PER_CYCLE))
    agrupados = {}
    for key, categoria in sports or []:
        if key and categoria in SUPPORTED_SPORTS:
            agrupados.setdefault(categoria, [])
            if key not in agrupados[categoria]:
                agrupados[categoria].append(key)

    selecionados = []
    for categoria in SUPPORTED_SPORTS:
        chaves = agrupados.get(categoria, [])
        if not chaves:
            continue
        indice = _sport_rotation.get(categoria, 0) % len(chaves)
        selecionados.append((chaves[indice], categoria))
        _sport_rotation[categoria] = (indice + 1) % len(chaves)
    return selecionados[:limite]


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
        sports = selecionar_feeds_para_ciclo(sports, config.ODDS_API_MAX_FEEDS_PER_CYCLE)
        if not sports:
            logger.warning("Nenhum esporte suportado disponível na Odds API nesta conta")
            return
        for sport_key, sport_name in sports:
            quota = get_quota_status()
            if quota["paused"]:
                logger.warning(
                    "Consultas de odds pausadas: %s créditos restantes; reserva mínima %s",
                    quota["remaining"], quota["reserve"],
                )
                set_service("odds_api", "warning", f"consultas pausadas; {quota['remaining']} créditos restantes")
                break
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
            atualizar_quota(resp.headers)
            if resp.status != 200:
                set_service("odds_api", "error", f"{sport_name}: HTTP {resp.status}")
                logger.warning("Odds API %s: HTTP %s", sport_name, resp.status)
                return
            odds_data = await resp.json()
            if not isinstance(odds_data, list):
                set_service("odds_api", "error", f"{sport_name}: resposta inválida")
                logger.warning("Resposta inesperada da Odds API para %s", sport_name)
                return
            set_service("odds_api", "ok", f"{sport_name}: {len(odds_data)} eventos consultados")
            for game in odds_data:
                if isinstance(game, dict) and game.get("bookmakers"):
                    await process_game(game, pm_markets, sport_name, session)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        set_service("odds_api", "error", f"{sport_name}: {type(exc).__name__}")
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
    top_ask_depth_usd = 0.0
    for level in book.get("asks", []):
        try:
            price, size = float(level["price"]), float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < price < 1) or not math.isfinite(size) or size <= 0:
            continue
        if best_ask is None:
            best_ask = price
            top_ask_depth_usd = price * size
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
                  if best_ask is not None and best_bid is not None and best_ask > best_bid else None)
    slippage_pct = ((average_price / best_ask) - 1) * 100 if best_ask else 0.0
    return {
        "stake_usd": cost, "average_price": average_price, "best_ask": best_ask,
        "best_bid": best_bid, "spread_pct": spread_pct, "slippage_pct": slippage_pct,
        "fee_usd": fee_usd, "gross_shares": gross_shares, "net_shares": net_shares,
        "top_ask_depth_usd": top_ask_depth_usd,
        "net_profit_usd": net_profit, "net_ev_pct": net_profit / cost * 100,
    }


def _book_age_seconds(timestamp):
    if timestamp is None:
        return None
    try:
        if isinstance(timestamp, (int, float)) or str(timestamp).strip().isdigit():
            value = float(timestamp)
            if value > 1e12:
                value /= 1000.0
            return max(0.0, time.time() - value)
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, time.time() - parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def avaliar_qualidade_livro(ev_data, stake_usd, max_spread_pct=None,
                         max_slippage_pct=None, min_top_ask_usd=None):
    """Aplica limites objetivos de spread, slippage e liquidez no melhor ask."""
    if not isinstance(ev_data, dict):
        return False, "book inválido"
    max_spread = config.ODDS_MAX_SPREAD_PCT if max_spread_pct is None else max_spread_pct
    max_slippage = config.ODDS_MAX_SLIPPAGE_PCT if max_slippage_pct is None else max_slippage_pct
    min_depth = config.ODDS_MIN_TOP_ASK_USD if min_top_ask_usd is None else min_top_ask_usd
    spread = _safe_metric(ev_data.get("spread_pct"))
    slippage = _safe_metric(ev_data.get("slippage_pct"))
    depth = _safe_metric(ev_data.get("top_ask_depth_usd"))
    if spread is None or slippage is None or depth is None:
        return False, "métricas do livro ausentes"
    if max_spread > 0 and spread > max_spread:
        return False, "spread acima do limite"
    if max_slippage > 0 and slippage > max_slippage:
        return False, "slippage acima do limite"
    required_depth = max(min_depth, float(stake_usd) * 0.25)
    if depth < required_depth:
        return False, "liquidez no melhor ask insuficiente"
    age = _safe_metric(ev_data.get("book_age_seconds"))
    if age is None:
        return False, "timestamp do livro ausente ou inválido"
    if age > config.ODDS_MAX_BOOK_AGE_SECONDS:
        return False, "livro de ofertas desatualizado"
    return True, "ok"


def _safe_metric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


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
        result = calcular_ev_liquido(fair_prob, book, schedule, stake_usd)
        if result is not None:
            result["book_age_seconds"] = _book_age_seconds(book.get("timestamp"))
        return result
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
        qualidade_ok, motivo = avaliar_qualidade_livro(ev_data, config.ODDS_ESTIMATED_STAKE_USD)
        if not qualidade_ok:
            logger.debug("Odds Value descartado (%s): %s", info["name"], motivo)
            continue
        key = f"{game.get('id', '')}:{normalize_team_name(info['name'])}:{pm_market.get('conditionId', pm_market.get('id', ''))}"
        now = time.monotonic()
        last_alert = _last_odds_alert.get(key)
        if last_alert is not None and now - last_alert < config.ODDS_COOLDOWN_SECONDS:
            continue
        enviado = await send_arbitrage_alert(
            game, info, info["odds"], fair_prob, ev_data, pm_market, token_id
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


async def send_arbitrage_alert(game, info, pinnacle_odds, fair_prob, ev_data, pm_market, token_id=None):
    try:
        slug = pm_market.get("slug")
        link = (
            config.POLYMARKET_BASE_URL + "/event/" + slug
            if slug else config.POLYMARKET_BASE_URL
        )
        # A classificação mede preço/liquidez/execução; não é probabilidade de acerto.
        qualidade = classificar_qualidade_odds(
            ev_data,
            minimum_edge_pct=config.ODDS_EDGE_THRESHOLD * 100,
            max_spread_pct=config.ODDS_MAX_SPREAD_PCT,
            max_slippage_pct=config.ODDS_MAX_SLIPPAGE_PCT,
            stake_usd=ev_data["stake_usd"],
            max_book_age_seconds=config.ODDS_MAX_BOOK_AGE_SECONDS,
        )
        message = (
            "🎯 OPORTUNIDADE ENCONTRADA\n\n"
            f"🏟️ {game.get('home_team', '')} x {game.get('away_team', '')}\n"
            f"🏆 {pm_market.get('_sport') or pm_market.get('sport') or 'Esporte'}\n"
            f"👉 Escolha: {info['name']}\n"
            f"📊 Chance estimada: {fair_prob * 100:.1f}%\n"
            f"💵 Valor analisado: ${ev_data['stake_usd']:.2f}\n"
            f"📈 Retorno esperado estimado: ${ev_data['net_profit_usd']:+.2f} "
            f"({ev_data['net_ev_pct']:+.1f}%)\n"
            f"🧭 Condições de preço e liquidez: {qualidade['label']} "
            f"({qualidade['score']}/{qualidade['max_score']})\n\n"
            "⚠️ Classificação baseada nas condições atuais de preço e liquidez; "
            "não prevê o resultado nem garante lucro.\n"
            f"🔗 Ver mercado: {link}"
        )
        enviado = await send_alert(message)
        if enviado:
            record_alert(
                "odds_value", "EV líquido", pm_market, token_id=token_id,
                outcome=info["name"], entry_price=ev_data["average_price"],
                reference_prob=fair_prob, ev_pct=ev_data["net_ev_pct"],
                stake_usd=ev_data["stake_usd"],
                details={"fee_usd": ev_data["fee_usd"], "spread_pct": ev_data["spread_pct"],
                         "slippage_pct": ev_data["slippage_pct"],
                         "gross_shares": ev_data["gross_shares"],
                         "net_shares": ev_data["net_shares"],
                         "quality_label": qualidade["label"],
                         "quality_score": qualidade["score"]},
            )
            logger.info("Oportunidade Odds Value enviada: %s", info["name"])
        else:
            logger.warning("Telegram não confirmou o alerta Odds Value: %s", info["name"])
        return bool(enviado)
    except Exception as exc:
        logger.error("Erro ao enviar alerta Odds Value: %s", type(exc).__name__)
        return False
