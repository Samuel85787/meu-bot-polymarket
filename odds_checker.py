import requests
import config
import asyncio
from telegram_notifier import send_alert
from gamma_api import fetch_active_markets

SPORTS_KEYS = {
    "basketball_nba": "NBA",
    "americanfootball_nfl": "NFL"
}

async def check_arbitrage(): 
    """Busca Odds Value (+EV) com cruzamento estrito de times"""
    if config.ODDS_API_KEY == "SUA_CHAVE_AQUI" or not config.ODDS_API_KEY:
        return

    print("\n🔍 Iniciando verificação de Odds Value (Pinnacle vs Polymarket)...")
    
    try:
        pm_markets = fetch_active_markets()
    except Exception as e:
        print(f"❌ Erro ao buscar mercados PM: {e}")
        return

    pm_dict = {}
    for m in pm_markets:
        pm_dict[m.get("question", "").lower()] = m

    for sport_key, sport_name in SPORTS_KEYS.items():
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
                continue
                
            odds_data = resp.json()
            
            for game in odds_data:
                if not game.get("bookmakers"):
                    continue
                    
                pinnacle = game["bookmakers"][0]
                market = pinnacle["markets"][0]
                
                # Pega os nomes dos times na Pinnacle
                home_team = game.get("home_team", "").lower()
                away_team = game.get("away_team", "").lower()
                
                # Pega a última palavra do nome do time (ex: "commanders", "eagles")
                home_kw = home_team.split()[-1]
                away_kw = away_team.split()[-1]

                for question, pm_market in pm_dict.items():
                    # 🛑 FILTRO ESTRITO: O jogo da PM precisa ter AMBOS os times
                    if home_kw in question and away_kw in question:
                        
                        # Agora vamos analisar as odds para cada time
                        for outcome in market["outcomes"]:
                            team_name = outcome["name"].lower()
                            pinnacle_odds = float(outcome["price"])
                            pinnacle_prob = 1 / pinnacle_odds
                            
                            # Descobre qual time é esse (casa ou fora)
                            is_home = home_kw in team_name
                            is_away = away_kw in team_name
                            
                            # 🆕 BUSCA O PREÇO EXATO DO TIME NA POLYMARKET
                            pm_price = None
                            for i, pm_outcome_name in enumerate(pm_market.get("outcomes", [])):
                                # Se o nome do time da PM for igual ao nome do time da Pinnacle
                                if (is_home and home_kw in pm_outcome_name.lower()) or \
                                   (is_away and away_kw in pm_outcome_name.lower()):
                                    pm_price = float(pm_market["outcomePrices"][i])
                                    break
                            
                            if pm_price is None:
                                continue
                            
                            # 🎯 FILTRO: 5% de vantagem
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
                                    f"💡 _Valor real detectado! A odd na PM está inflada._\n\n"
                                    f"[👉 ABRIR NA POLYMARKET]({link})"
                                )
                                await send_alert(msg)
                                print(f"🚀 Oportunidade: {outcome['name']} (Odd PM {1/pm_price:.2f} vs Odd Pinnacle {pinnacle_odds:.2f})")
                                break 
                                
        except Exception as e:
            print(f"❌ Erro ao checar {sport_name}: {e}")
            
        await asyncio.sleep(2)
    
    print("🏁 Verificação finalizada.\n")
