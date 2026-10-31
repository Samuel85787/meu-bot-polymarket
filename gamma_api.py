import json
import requests
from config import MAX_MARKETS_TO_MONITOR

GAMMA_BASE = "https://gamma-api.polymarket.com"

# IDs corrigidos com base no primaryTagId do seu navegador
TAG_IDS = {
    "NFL": "450",
    "NBA": "10345",
    "ATP (Tênis)": "101232",
    "WTA (Tênis)": "102123",
    "VALORANT": "101672",
    "CS2": "100780"
}

def fetch_active_markets(limit: int = MAX_MARKETS_TO_MONITOR):
    all_markets = []
    
    # Faz a busca para cada esporte separadamente
    for sport, tag_id in TAG_IDS.items():
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": tag_id,  # Envia um ID por vez
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            print(f"   [+] {len(markets)} mercados encontrados para {sport}")
        except requests.exceptions.RequestException as e:
            print(f"   [!] Erro ao buscar mercados para {sport}: {e}")

    # Remove duplicatas (caso um mercado apareça em mais de uma busca)
    unique_markets = list({m['id']: m for m in all_markets}.values())
    
    # Processa os campos JSON que vêm como string
    for m in unique_markets:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    # Retorna a lista final, respeitando o limite máximo definido no config.py
    return unique_markets[:limit]
