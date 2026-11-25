import asyncio
import logging
import time

import aiohttp
import config
from gamma_api import fetch_active_markets
from telegram_notifier import send_alert

logger = logging.getLogger(__name__)

SPORTS_KEYS = {
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL",
}

_last_odds_alert = {}


def normalize_team_name(name):
    return str(name or "").lower().strip()


def teams_match(home_team, away_team, question):
    q = normalize_team_name(question)
    home_parts = [p for p in normalize_team_name(home_team).split() if len(p) > 2]
    away_parts = [p for p in normalize_team_name(away_team).split() if len(p) > 2]

    home_kw = home_parts[-1] if home_parts else normalize_team_name(home_team)
    away_kw = away_parts[-1] if away_parts else normalize_team_name(away_team)

    return bool(home_kw and away_kw and home_kw in q and away_kw in q)


def calcular_probabilidades_sem_vig(outcomes):
    """Remove o overround (margem) somando 1/odd de todos os desfechos."""
    implicadas = {}
    soma_inversa = 0.0

    for item in outcomes:
        nome = str(item.get("name", "")).strip()
        try:
            odd = float(item.get("price", 0))
        except (TypeError, ValueError):
            continue
        if odd > 1.0:
            p = 1.0 / odd
            implicadas[nome] = (odd, p)
            soma_inversa += p

    if soma_inversa <= 0:
        return {}

    justas = {}
    for nome, (odd, p) in implicadas.items():
        justas[nome.lower()] = {
            "name": nome,
            "odds": odd,
            "raw_prob": p,
            "fair_prob": p / soma_inversa,
        }
    return justas


async def check_arbitrage(pm_markets=None):
    if not config.ODDS_API_KEY:
        logger.warning("ODDS_API_KEY nao configurada")
        return

    logger.info("Verificando Odds Value")

    try:
        if pm_markets is None:
            pm_markets = await fetch_active_markets()
    except Exception as e:
        logger.error("Erro ao buscar mercados: %s", e)
        return

    pm_dict = {}
    for m in pm_markets:
        question_normalized = normalize_team_name(m.get("question", ""))
        if question_normalized:
            pm_dict[question_normalized] = m

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for sport_key, sport_name in SPORTS_KEYS.items():
            try:
                await check_sport_arbitrage(session, sport_key, sport_name, pm_dict)
            except Exception as e:
                logger.error("Erro em %s: %s", sport_name, e)
            await asyncio.sleep(2)

    logger.info("Verificacao finalizada")


async def check_sport_arbitrage(session, sport_key, sport_name, pm_dict):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": "eu",
        "bookmakers": "pinnacle",
        "markets": "h2h",
    }

    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                logger.error("Erro %s: HTTP %s", sport_name, resp.status)
                return

            odds_data = await resp.json()
            if not isinstance(odds_data, list):
                return

            for game in odds_data:
                if not isinstance(game, dict) or not game.get("bookmakers"):
                    continue
                await process_game(game, pm_dict)

    except Exception as e:
        logger.error("Falha %s: %s", sport_name, e)


async def process_game(game, pm_dict):
    try:
        pinnacle = next(
            (b for b in game.get("bookmakers", []) if str(b.get("key", "")).lower() == "pinnacle"),
            None,
        )
        if not pinnacle or not pinnacle.get("markets"):
            return

        market = pinnacle["markets"][0]
        home_team = normalize_team_name(game.get("home_team", ""))
        away_team = normalize_team_name(game.get("away_team", ""))

        for question, pm_market in pm_dict.items():
            if not teams_match(home_team, away_team, question):
                continue
            await check_outcomes(game, market, pm_market, home_team, away_team)

    except (KeyError, IndexError, TypeError) as e:
        logger.warning("Erro ao processar jogo: %s", e)


async def check_outcomes(game, pinnacle_market, pm_market, home_team, away_team):
    try:
        fair_map = calcular_probabilidades_sem_vig(pinnacle_market.get("outcomes", []))
        if not fair_map:
            return

        home_kw = home_team.split()[-1] if home_team else ""
        away_kw = away_team.split()[-1] if away_team else ""

        for _, info in fair_map.items():
            team_name = info["name"].lower()
            fair_prob = info["fair_prob"]
            pinnacle_odds = info["odds"]

            is_home = bool(home_kw and home_kw in team_name)
            is_away = bool(away_kw and away_kw in team_name)

            pm_price = find_pm_price(pm_market, is_home, is_away, home_team, away_team)
            if pm_price is None or pm_price <= 0 or pm_price >= 1:
                continue

            if pm_price < (fair_prob - config.ODDS_EDGE_THRESHOLD):
                chave = f"{game.get('id', '')}_{team_name}"
                agora = time.time()
                if agora - _last_odds_alert.get(chave, 0) < config.ODDS_COOLDOWN_SECONDS:
                    continue
                _last_odds_alert[chave] = agora

                await send_arbitrage_alert(
                    game, info, pinnacle_odds, fair_prob, pm_price, pm_market
                )

    except (KeyError, ValueError, IndexError) as e:
        logger.warning("Erro outcomes: %s", e)


def find_pm_price(pm_market, is_home, is_away, home_team, away_team):
    try:
        home_kw = normalize_team_name(home_team).split()[-1] if home_team else ""
        away_kw = normalize_team_name(away_team).split()[-1] if away_team else ""
        prices = pm_market.get("outcomePrices", [])

        for i, pm_outcome_name in enumerate(pm_market.get("outcomes", [])):
            pm_outcome_normalized = normalize_team_name(pm_outcome_name)
            if (is_home and home_kw and home_kw in pm_outcome_normalized) or (
                is_away and away_kw and away_kw in pm_outcome_normalized
            ):
                if i < len(prices):
                    return float(prices[i])
        return None
    except (IndexError, ValueError) as e:
        logger.warning("Erro ao encontrar preco: %s", e)
        return None


async def send_arbitrage_alert(game, info, pinnacle_odds, fair_prob, pm_price, pm_market):
    try:
        link = config.POLYMARKET_BASE_URL + "/event/" + pm_market.get("slug", "")
        ev_advantage = (fair_prob - pm_price) * 100

        msg = (
            "ODDS VALUE DETECTADO\n"
            f"Jogo: {game.get('home_team', '')} vs {game.get('away_team', '')}\n"
            f"Palpite: {info['name']}\n"
            f"Pinnacle Justo (sem vig): {round(fair_prob * 100, 1)}% (Odd {round(pinnacle_odds, 2)})\n"
            f"Polymarket: {round(pm_price * 100, 1)}% (Odd {round(1 / pm_price, 2)})\n"
            f"Vantagem (+EV): +{round(ev_advantage, 1)}%\n"
            f"Link: {link}"
        )
        await send_alert(msg)
        logger.info("Oportunidade: %s", info["name"])
    except Exception as e:
        logger.error("Erro ao enviar alerta arbitrage: %s", e)
