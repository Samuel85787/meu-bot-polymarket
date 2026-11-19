import requests
import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import config
from telegram_notifier import send_alert
from gamma_api import fetch_active_markets

logger = logging.getLogger(__name__)

SPORTS_KEYS = {
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL"
}

def normalize_team_name(name: str) -> str:
    """Normaliza nomes de times para melhor matching"""
    return name.lower().strip()

def teams_match(home_team: str, away_team: str, question: str) -> bool:
    """
    Verifica se ambos os times estão presentes na questão do mercado.
    
    Args:
        home_team: Nome do time mandante
        away_team: Nome do time visitante
        question: Questão do mercado
    
    Returns:
        bool: True se ambos os times foram encontrados
    """
    q = question.lower()
    home_normalized = normalize_team_name(home_team)
    away_normalized = normalize_team_name(away_team)
    
    # Pega a última palavra (nome principal do time)
    home_kw = home_normalized.split()[-1]
    away_kw = away_normalized.split()[-1]
    
    return home_kw in q and away_kw in q

async def check_arbitrage() -> None:
    """
    Busca Odds Value (+EV) com cruzamento estrito de times.
    Compara odds da Pinnacle (justo) com Polymarket (pode estar barato).
    """
    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "SUA_CHAVE_AQUI":
        logger.warning("⚠️ ODDS_API_KEY não configurada. Verificação de Odds Value desativada.")
        return

    logger.info("\n🔍 Iniciando verificação de Odds Value (Pinnacle vs Polymarket)...")
    
    try:
        pm_markets = fetch_active_markets()
    except Exception as e:
        logger.error(f"❌ Erro ao buscar mercados PM: {type(e).__name__}: {e}")
        return

    # Cria dicionário para busca rápida
    pm_dict: Dict[str, Dict] = {}
    for m in pm_markets:
        question_normalized = m.get("question", "").lower()
        pm_dict[question_normalized] = m

    for sport_key, sport_name in SPORTS_KEYS.items():
        try:
            await check_sport_arbitrage(sport_key, sport_name, pm_dict)
        except Exception as e:
            logger.error(f"❌ Erro ao checar {sport_name}: {type(e).__name__}: {e}")
        
        await asyncio.sleep(2)
    
    logger.info("🏁 Verificação de Odds Value finalizada.\n")

async def check_sport_arbitrage(sport_key: str, sport_name: str, pm_dict: Dict[str, Dict]) -> None:
    """
    Verifica arbitragem para um esporte específico.
    
    Args:
        sport_key: Chave do esporte na API
        sport_name: Nome do esporte
        pm_dict: Dicionário de mercados Polymarket
    """
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": "eu",
        "bookmakers": "pinnacle",
        "markets": "h2h"
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Erro ao buscar odds para {sport_name}: Status {resp.status_code}")
            return
            
        odds_data = resp.json()
        
        for game in odds_data:
            if not game.get("bookmakers"):
                continue
            
            await process_game(game, sport_name, pm_dict)
            
    except requests.exceptions.Timeout:
        logger.error(f"Timeout ao buscar odds para {sport_name}")
    except Exception as e:
        logger.error(f"Erro ao buscar odds para {sport_name}: {type(e).__name__}: {e}")

async def process_game(game: Dict, sport_name: str, pm_dict: Dict[str, Dict]) -> None:
    """
    Processa um jogo individual buscando oportunidades de +EV.
    
    Args:
        game: Dados do jogo da API de odds
        sport_name: Nome do esporte
        pm_dict: Dicionário de mercados Polymarket
    """
    try:
        pinnacle = game["bookmakers"][0]
        market = pinnacle["markets"][0]
        
        home_team = game.get("home_team", "").lower()
        away_team = game.get("away_team", "").lower()
        
        for question, pm_market in pm_dict.items():
            # Verifica se ambos os times estão no mercado
            if not teams_match(home_team, away_team, question):
                continue
            
            await check_outcomes(game, market, pm_market, home_team, away_team)
            
    except (KeyError, IndexError) as e:
        logger.warning(f"Erro ao processar jogo: {type(e).__name__}: {e}")

async def check_outcomes(game: Dict, pinnacle_market: Dict, pm_market: Dict,
                        home_team: str, away_team: str) -> None:
    """
    Verifica cada outcome do jogo buscando +EV.
    
    Args:
        game: Dados do jogo
        pinnacle_market: Mercado da Pinnacle
        pm_market: Mercado da Polymarket
        home_team: Nome do time mandante
        away_team: Nome do time visitante
    """
    try:
        for outcome in pinnacle_market["outcomes"]:
            team_name = outcome["name"].lower()
            pinnacle_odds = float(outcome["price"])
            pinnacle_prob = 1 / pinnacle_odds
            
            is_home = home_team.split()[-1] in team_name
            is_away = away_team.split()[-1] in team_name
            
            # Busca o preço exato do time na Polymarket
            pm_price = find_pm_price(pm_market, is_home, is_away, home_team, away_team)
            
            if pm_price is None:
                continue
            
            # Verifica se há +EV (5% de vantagem)
            ev_threshold = 0.05
            if pm_price < (pinnacle_prob - ev_threshold):
                await send_arbitrage_alert(
                    game, outcome, pinnacle_odds, pinnacle_prob,
                    pm_price, pm_market
                )
                
    except (KeyError, ValueError, IndexError) as e:
        logger.warning(f"Erro ao checar outcomes: {type(e).__name__}: {e}")

def find_pm_price(pm_market: Dict, is_home: bool, is_away: bool,
                 home_team: str, away_team: str) -> Optional[float]:
    """
    Encontra o preço exato do time na Polymarket.
    
    Args:
        pm_market: Mercado Polymarket
        is_home: Se é time mandante
        is_away: Se é time visitante
        home_team: Nome do time mandante
        away_team: Nome do time visitante
    
    Returns:
        float: Preço do outcome ou None se não encontrado
    """
    try:
        home_kw = normalize_team_name(home_team).split()[-1]
        away_kw = normalize_team_name(away_team).split()[-1]
        
        for i, pm_outcome_name in enumerate(pm_market.get("outcomes", [])):
            pm_outcome_normalized = pm_outcome_name.lower()
            
            if (is_home and home_kw in pm_outcome_normalized) or \
               (is_away and away_kw in pm_outcome_normalized):
                return float(pm_market["outcomePrices"][i])
        
        return None
    except (IndexError, ValueError) as e:
        logger.warning(f"Erro ao encontrar preço PM: {type(e).__name__}: {e}")
        return None

async def send_arbitrage_alert(game: Dict, outcome: Dict, pinnacle_odds: float,
                              pinnacle_prob: float, pm_price: float,
                              pm_market: Dict) -> None:
    """
    Envia alerta de oportunidade de +EV.
    
    Args:
        game: Dados do jogo
        outcome: Outcome do mercado
        pinnacle_odds: Odd da Pinnacle
        pinnacle_prob: Probabilidade implícita da Pinnacle
        pm_price: Preço na Polymarket
        pm_market: Mercado Polymarket
    """
    try:
        link = f"{config.POLYMARKET_BASE_URL}/event/{pm_market.get('slug', '')}"
        ev_advantage = (pinnacle_prob - pm_price) * 100
        
        msg = (
            f"💎 *ODDS VALUE (+EV) DETECTADO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏆 *Jogo:* {game['home_team']} vs {game['away_team']}\n"
            f"🎯 *Palpite:* `{outcome['name']}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📉 *Pinnacle (Justo):* {pinnacle_prob*100:.1f}% (Odd {pinnacle_odds:.2f})\n"
            f"🟢 *Polymarket (Barato):* {pm_price*100:.1f}% (Odd {1/pm_price:.2f})\n"
            f"🔥 *Vantagem (+EV):* +{ev_advantage:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 _Valor real detectado! A odd na PM está inflada._\n\n"
            f"[👉 ABRIR NA POLYMARKET]({link})"
        )
        await send_alert(msg)
        logger.info(f"🚀 Oportunidade: {outcome['name']} (+EV {ev_advantage:.1f}%)")
        
    except Exception as e:
        logger.error(f"Erro ao enviar alerta de arbitragem: {type(e).__name__}: {e}")