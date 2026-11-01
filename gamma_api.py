import json
import requests
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"

# IDs dos esportes
TAG_IDS = {
    "NFL": "450",
    "NBA": "10345",
    "ATP (Tênis)": "101232",
    "WTA (Tênis)": "102123",
    "VALORANT": "101672",
    "CS2": "100780"
}

# 🎯 COTAS POR ESPORTE: Ajuste os números como preferir.
# Tênis e e-sports têm cotas maiores para capturar mais jogos.
LIMITES_POR_ESPORTE = {
    "NFL": 30,          # Cota menor para NFL
    "NBA": 40,
    "ATP (Tênis)": 100, # Cota alta para tênis
    "WTA (Tênis)": 100, # Cota alta para tênis
    "VALORANT": 80,     # Cota alta para e-sports
    "CS2": 80           # Cota alta para e-sports
}

TIPOS_PERMITIDOS = [
    "moneyline", "spreads", "totals",
    "anytime_touchdowns", "passing_yards", "rushing_yards",
    "receiving_yards", "passing_touchdowns",
    "points", "rebounds", "assists",
    "assists_points_rebounds", "double_doubles",
    "basketball_player_double_double", "basketball_player_triple_double",
    "first_blood_game", "total_kills"
]

def fetch_active_markets():
    all_markets = []
    
    for sport, tag_id in TAG_IDS.items():
        # Pega a cota específica do esporte (se não existir, usa 50 como padrão)
        limite_esporte = LIMITES_POR_ESPORTE.get(sport, 50)
        
        params = {
            "active": "true",
            "closed": "false",
            "limit": limite_esporte,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": tag_id,
            "sports_market_types": TIPOS_PERMITIDOS,
            "liquidity_num_min": 5000,
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            print(f"   [+] {len(markets)} mercados encontrados para {sport} (cota: {limite_esporte})")
        except requests.exceptions.RequestException as e:
            print(f"   [!] Erro ao buscar mercados para {sport}: {e}")

    # Remove duplicatas
    unique_markets = list({m['id']: m for m in all_markets}.values())
    
    for m in unique_markets:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    return unique_markets
