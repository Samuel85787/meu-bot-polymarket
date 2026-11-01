import json
import requests
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"

# 🎯 IDs DE TAG CORRIGIDOS E CONFIRMADOS
TAG_IDS = {
    "NBA": "1",          # Tag principal de Esportes; o filtro de tipos refina para jogos
    "Tênis": "864",      # ID confirmado para todos os mercados de tênis
    "E-sports": "64",    # ID base para e-sports; pode ser necessário ajustar
    "NFL": "450"         # Mantido, mas com cota menor
}

# 🎯 COTAS POR ESPORTE (ajuste como preferir)
LIMITES_POR_ESPORTE = {
    "NBA": 100,          # Cota alta para NBA
    "Tênis": 150,        # Cota muito alta para Tênis
    "E-sports": 100,     # Cota alta para E-sports
    "NFL": 30            # Cota menor para NFL
}

# 🎯 TIPOS DE MERCADO: Foco em jogos, não em futuros
TIPOS_PERMITIDOS = [
    "moneyline", "spreads", "totals",
    "player_props", "team_props"
]

def fetch_active_markets():
    all_markets = []
    
    for sport, tag_id in TAG_IDS.items():
        limite = LIMITES_POR_ESPORTE.get(sport, 50)
        
        params = {
            "active": "true",
            "closed": "false",
            "limit": limite,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": tag_id,
            "sports_market_types": TIPOS_PERMITIDOS,
            "liquidity_num_min": 1000, # Reduzido para capturar mais mercados
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            print(f"   [+] {len(markets)} mercados encontrados para {sport} (tag: {tag_id})")
        except requests.exceptions.RequestException as e:
            print(f"   [!] Erro ao buscar mercados para {sport}: {e}")

    # Remove duplicatas
    unique_markets = list({m['id']: m for m in all_markets}.values())
    
    for m in unique_markets:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    return unique_markets
