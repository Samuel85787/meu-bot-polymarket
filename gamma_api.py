import json
import requests
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"

# 🎯 IDs de tag corretos para cada esporte
ESPORTES = {
    "NBA": {"tag_id": "745"},        # Tag específica da NBA
    "NFL": {"tag_id": "450"},
    "Tênis ATP": {"tag_id": "101232"},
    "Tênis WTA": {"tag_id": "102123"},
    "CS2": {"tag_id": "100780"},
    "VALORANT": {"tag_id": "101672"}
}

# 🎯 Cota de mercados por esporte
LIMITES_POR_ESPORTE = {
    "NBA": 100,
    "NFL": 30,
    "Tênis ATP": 80,
    "Tênis WTA": 80,
    "CS2": 80,
    "VALORANT": 80
}

# 🎯 Foco exclusivo em moneyline (evita futuros e outros tipos)
TIPOS_PERMITIDOS = ["moneyline"]

# 🎯 Tag de futebol para excluir (ID 100350)
TAG_FUTEBOL = "100350"

def fetch_active_markets():
    all_markets = []
    
    for sport, dados in ESPORTES.items():
        limite = LIMITES_POR_ESPORTE.get(sport, 50)
        
        params = {
            "active": "true",
            "closed": "false",
            "limit": limite,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": dados["tag_id"],          # Tag específica do esporte
            "sports_market_types": TIPOS_PERMITIDOS,
            "liquidity_num_min": 1000,
            "exclude_tag_id": TAG_FUTEBOL      # Exclui futebol
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            print(f"   [+] {len(markets)} mercados encontrados para {sport}")
        except requests.exceptions.RequestException as e:
            print(f"   [!] Erro ao buscar mercados para {sport}: {e}")

    # Remove duplicatas
    unique_markets = list({m['id']: m for m in all_markets}.values())
    
    # 🛡️ Filtro de segurança: remove qualquer mercado que ainda mencione futebol
    filtrados = []
    for m in unique_markets:
        titulo = m.get("question", "").lower()
        if "soccer" in titulo or "football" in titulo:
            continue
        filtrados.append(m)
    
    # Processa os campos JSON
    for m in filtrados:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    return filtrados
