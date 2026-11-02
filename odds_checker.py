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
    """Busca Odds Value (+EV) comparando a Pinnacle com a Polymarket"""
    if config.ODDS_API_KEY == "SUA_CHAVE_AQUI" or not config.ODDS_API_KEY:
        print("⚠️ Chave da Odds API não configurada no config.py.")
        return

    print("\n🔍 Iniciando verificação de Odds Value (Pinnacle vs Polymarket)...")
    
    try:
        pm_markets = fetch_active_markets()
        print(f"📊 {len(pm_markets)} mercados da Polymarket carregados para comparação.")
    except Exception as e:
        print(f"❌ Erro ao buscar mercados PM: {e}")
        return

    pm_dict = {}
    for m in pm_markets:
        question = m.get("question", "").lower()
        pm_dict[question] = m

    for sport_key, sport_name in SPORTS_KEYS.items():
        print(f"📡 Buscando odds da Pinnacle para {sport_name}...")
        url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
        params = {
            "apiKey": config.ODDS_API_KEY,
            "regions": "eu",
            "bookmakers": "pinnacle",
            "markets": "h2h"
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            remaining = resp.headers.get("x-requests-remaining", "Desconhecido")
            print(f"✅ Resposta recebida. Requisições restantes na API: {remaining}")
            
            if resp.status_code != 200:
                print(f"❌ Erro na API Odds ({sport_name}): {resp.status_code} - {resp.text}")
                continue
                
            odds_data = resp.json()
            print(f"   [+] {len(odds_data)} jogos encontrados na Pinnacle para {sport_name}")
            
            for game in odds_data:
                if not game.get("bookmakers"):
                    continue
                    
                pinnacle = game["bookmakers"][0]
                market = pinnacle["markets"][0]
                
                for outcome in market["outcomes"]:
                    team_name = outcome["name"].lower()
                    pinnacle_odds = float(outcome["price"])
                    pinnacle_prob = 1 / pinnacle_odds
                    
                    # Pega a última palavra do time (ex: "seahawks")
                    team_keyword = team_name.split()[-1] 
                    
                    for question, pm_market in pm_dict.items():
                        if team_keyword in question:
                            
                            # 🆕 A CORREÇÃO ESTÁ AQUI: Achar o índice exato do time
                            pm_price = None
                            for i, pm_outcome_name in enumerate(pm_market.get("outcomes", [])):
                                if team_keyword in pm_outcome_name.lower():
                                    pm_price = float(pm_market["outcomePrices"][i])
                                    break
                            
                            # Se não achou o time específico, pula
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
                                print(f"🚀 Oportunidade encontrada: {outcome['name']} (+{((pinnacle_prob - pm_price) * 100):.1f}%)")
                                break 
                                
        except Exception as e:
            print(f"❌ Erro ao checar {sport_name}: {e}")
            
        await asyncio.sleep(2)
    
    print("🏁 Verificação de Odds Value finalizada.\n")
