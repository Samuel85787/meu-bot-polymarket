import asyncio
import logging

import aiohttp
import config
from telegram_notifier import send_alert
from gamma_api import fetch_active_markets

logger = logging.getLogger(__name__)

SPORTS_KEYS = {
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL"
}

def normalize_team_name(name):
    return name.lower().strip()

def teams_match(home_team, away_team, question):
    q = question.lower()
    home_normalized = normalize_team_name(home_team)
    away_normalized = normalize_team_name(away_team)
    
    home_kw = home_normalized.split()[-1]
    away_kw = away_normalized.split()[-1]
    
    return home_kw in q and away_kw in q

async def check_arbitrage():
    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "SUA_CHAVE_AQUI":
        logger.warning("ODDS_API_KEY nao configurada")
        return

    logger.info("Verificando Odds Value")
    
    try:
        pm_markets = await fetch_active_markets()
    except Exception as e:
        logger.error("Erro ao buscar mercados: " + str(e))
        return

    pm_dict = {}
    for m in pm_markets:
        question_normalized = m.get("question", "").lower()
        pm_dict[question_normalized] = m

    for sport_key, sport_name in SPORTS_KEYS.items():
        try:
            await check_sport_arbitrage(sport_key, sport_name, pm_dict)
        except Exception as e:
            logger.error("Erro em " + sport_name + ": " + str(e))
        
        await asyncio.sleep(2)
    
    logger.info("Verificacao finalizada")

async def check_sport_arbitrage(sport_key, sport_name, pm_dict):
    url = "https://api.the-odds-api.com/v4/sports/" + sport_key + "/odds/"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": "eu",
        "bookmakers": "pinnacle",
        "markets": "h2h"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.error("Erro " + sport_name + ": " + str(resp.status))
                    return
                
                odds_data = await resp.json()
                
                for game in odds_data:
                    if not game.get("bookmakers"):
                        continue
                    
                    await process_game(game, sport_name, pm_dict)
    
    except Exception as e:
        logger.error("Falha " + sport_name + ": " + str(e))

async def process_game(game, sport_name, pm_dict):
    try:
        pinnacle = game["bookmakers"][0]
        market = pinnacle["markets"][0]
        
        home_team = game.get("home_team", "").lower()
        away_team = game.get("away_team", "").lower()
        
        for question, pm_market in pm_dict.items():
            if not teams_match(home_team, away_team, question):
                continue
            
            await check_outcomes(game, market, pm_market, home_team, away_team)
    
    except (KeyError, IndexError) as e:
        logger.warning("Erro ao processar jogo: " + str(e))

async def check_outcomes(game, pinnacle_market, pm_market, home_team, away_team):
    try:
        for outcome in pinnacle_market["outcomes"]:
            team_name = outcome["name"].lower()
            pinnacle_odds = float(outcome["price"])
            pinnacle_prob = 1 / pinnacle_odds
            
            is_home = home_team.split()[-1] in team_name
            is_away = away_team.split()[-1] in team_name
            
            pm_price = find_pm_price(pm_market, is_home, is_away, home_team, away_team)
            
            if pm_price is None:
                continue
            
            ev_threshold = 0.05
            if pm_price < (pinnacle_prob - ev_threshold):
                await send_arbitrage_alert(game, outcome, pinnacle_odds, pinnacle_prob, pm_price, pm_market)
    
    except (KeyError, ValueError, IndexError) as e:
        logger.warning("Erro outcomes: " + str(e))

def find_pm_price(pm_market, is_home, is_away, home_team, away_team):
    try:
        home_kw = normalize_team_name(home_team).split()[-1]
        away_kw = normalize_team_name(away_team).split()[-1]
        
        for i, pm_outcome_name in enumerate(pm_market.get("outcomes", [])):
            pm_outcome_normalized = pm_outcome_name.lower()
            
            if (is_home and home_kw in pm_outcome_normalized) or (is_away and away_kw in pm_outcome_normalized):
                return float(pm_market["outcomePrices"][i])
        
        return None
    except (IndexError, ValueError) as e:
        logger.warning("Erro ao encontrar preco: " + str(e))
        return None

async def send_arbitrage_alert(game, outcome, pinnacle_odds, pinnacle_prob, pm_price, pm_market):
    try:
        link = config.POLYMARKET_BASE_URL + "/event/" + pm_market.get("slug", "")
        ev_advantage = (pinnacle_prob - pm_price) * 100
        
        msg = (
            "ODDS VALUE DETECTADO\n"
            "Jogo: " + game["home_team"] + " vs " + game["away_team"] + "\n"
            "Palpite: " + outcome["name"] + "\n"
            "Pinnacle (Justo): " + str(round(pinnacle_prob * 100, 1)) + "% (Odd " + str(round(pinnacle_odds, 2)) + ")\n"
            "Polymarket (Barato): " + str(round(pm_price * 100, 1)) + "% (Odd " + str(round(1/pm_price, 2)) + ")\n"
            "Vantagem (+EV): +" + str(round(ev_advantage, 1)) + "%\n"
            "Link: " + link
        )
        await send_alert(msg)
        logger.info("Oportunidade: " + outcome["name"])
    
    except Exception as e:
        logger.error("Erro ao enviar alerta arbitrage: " + str(e))