import requests
import config
import asyncio
from telegram_notifier import send_alert
from gamma_api import fetch_active_markets

# Esportes suportados pela The Odds API
SPORTS_KEYS = {
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL",
    "tennis_atp": "ATP"
}

async def check_arbitrage(): 
    """Busca Odds Value (+EV) comparando a Pinnacle com a Polymarket"""
    if config.ODDS_API_KEY == "SUA_CHAVE_AQUI":
        print("⚠️ Chave da Odds API não configurada.")
        return

    print("🔍 Iniciando verificação de Odds Value...")
    
    try:
        pm_markets = fetch_active_markets()
    except Exception as e:
        print(f"Erro ao buscar mercados PM: {e}")
        return

    # Mapeia os mercados da PM pela pergunta
    pm_dict = {}
    for m in pm_markets:
        question = m.get("question", "").lower()
        pm_dict[question] = m

    for sport_key, sport_name in SPORTS_KEYS.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        params = {
            "apiKey": config.ODDS_API_KEY,
            "regions": "eu", # Pinnacle costuma estar na Europa
            "bookmakers": "pinnacle",
            "markets": "h2h" # Moneyline
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                print(f"Erro na API Odds ({sport_name}): {resp.status_code}")
                continue
                
            odds_data = resp.json()
            
            for game in odds_data:
                if not game.get("bookmakers"):
                    continue
                    
                pinnacle = game["bookmakers"][0]
                market = pinnacle["markets"][0]
                
                for outcome in market["outcomes"]:
                    team_name = outcome["name"].lower()
                    pinnacle_odds = float(outcome["price"])
                    
                    # Probabilidade implícita da Pinnacle
                    pinnacle_prob = 1 / pinnacle_odds
                    
                    # Pega a última palavra do time (ex: "lakers")
                    team_keyword = team_name.split()[-1] 
                    
                    for question, pm_market in pm_dict.items():
                        if team_keyword in question:
                            pm_price = float(pm_market["outcomePrices"][0])
                            
                            # 🎯 FILTRO DE VALOR (+EV): 5% ou mais de vantagem
                            if pm_price < (pinnacle_prob - 0.05): 
                                link = f"{config.POLYMARKET_BASE_URL}/event/{pm_market['slug']}"
                                
                                msg = (
                                    f"💎 *ODDS VALUE (+EV) DETECTADO*\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"🏆 *Jogo:* {game['home_team']} vs {game['away_team']}\n"
                                    f"🎯 *Palpite:* `{outcome['name']}`\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"📉 *Pinnacle (Justo):* {pinnacle_prob*100:.1f}% (Odd {pinnacle_odds:.2f})\n"
                                    f"🟢 *Polymarket (Barato):* {pm_price*100:.1f}% (Odd {1/pm_price:.2f})\n"
                                    f"🔥 *Vantagem (+EV):* +{((pinnacle_prob - pm_price) * 100):.1f}%\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"💡 _A Polymarket está pagando mais do que a probabilidade real. Valor a longo prazo!_\n\n"
                                    f"[👉 ABRIR NA POLYMARKET]({link})"
                                )
                                await send_alert(msg)
                                break 
                                
        except Exception as e:
            print(f"Erro ao checar {sport_name}: {e}")
            
        await asyncio.sleep(2) # Pausa para não estourar o limite
