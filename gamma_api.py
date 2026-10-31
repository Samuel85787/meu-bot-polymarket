import json
import requests
from config import MAX_MARKETS_TO_MONITOR

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Os IDs exatos que você me passou
TAG_IDS = {
    "NFL": "1,450,100639",
    "NBA": "1,745,100639",
    "ATP (Tênis)": "1,864,100639,101232",
    "WTA (Tênis)": "1,864,100639,102123",
    "VALORANT": "1,64,101672,100639",
    "CS2": "1,64,100780,100639"
}

def fetch_active_markets(limit: int = MAX_MARKETS_TO_MONITOR):
    all_markets = []
    
    # Faz a busca para cada esporte
    for sport, tag_id_str in TAG_IDS.items():
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": tag_id_str,  # A API aceita múltiplos IDs separados por vírgula
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            print(f"   [+] {len(markets)} mercados encontrados para {sport}")
        except requests.exceptions.RequestException as e:
            print(f"   [!] Erro ao buscar mercados para {sport}: {e}")

    # Remove duplicatas (um mercado pode ter várias tags e aparecer mais de uma vez)
    unique_markets = list({m['id']: m for m in all_markets}.values())
    
    # Processa os campos JSON que vêm como string
    for m in unique_markets:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    # Retorna a lista final, respeitando o limite máximo definido no config.py
    return unique_markets[:limit]

def get_market_by_slug(slug: str):
    resp = requests.get(f"{GAMMA_BASE}/markets/slug/{slug}", timeout=10)
    resp.raise_for_status()
    return resp.json()