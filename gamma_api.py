import json
import requests
from config import MAX_MARKETS_TO_MONITOR

GAMMA_BASE = "https://gamma-api.polymarket.com"

TAG_IDS = {
    "NFL": "450",
    "NBA": "10345",
    "ATP (Tênis)": "101232",
    "WTA (Tênis)": "102123",
    "VALORANT": "101672",
    "CS2": "100780"
}

# 🎯 Tipos de mercado permitidos (game lines + props populares)
# Remova ou adicione itens conforme desejar.
TIPOS_PERMITIDOS = [
    "moneyline",                 # Vencedor da partida
    "spreads",                   # Handicap de pontos
    "totals",                    # Over/Under
    # --- Props de NFL ---
    "anytime_touchdowns",
    "passing_yards",
    "rushing_yards",
    "receiving_yards",
    "passing_touchdowns",
    # --- Props de NBA ---
    "points",
    "rebounds",
    "assists",
    "assists_points_rebounds",
    "double_doubles",
    # --- Props de CS2/Valorant ---
    "first_blood_game",
    "total_kills",
]

def fetch_active_markets(limit: int = MAX_MARKETS_TO_MONITOR):
    all_markets = []
    
    for sport, tag_id in TAG_IDS.items():
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": tag_id,
            "sports_market_types": TIPOS_PERMITIDOS,
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            print(f"   [+] {len(markets)} mercados encontrados para {sport}")
        except requests.exceptions.RequestException as e:
            print(f"   [!] Erro ao buscar mercados para {sport}: {e}")

    unique_markets = list({m['id']: m for m in all_markets}.values())
    
    for m in unique_markets:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    return unique_markets[:limit]
