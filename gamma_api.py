import json
import requests
import config

GAMMA_BASE = "https://gamma-api.polymarket.com"

# 🎯 CONFIGURAÇÃO CORRETA POR ESPORTE
ESPORTES = {
    "NBA": {"param": "series_id", "id": "10345"},          # ID de série da NBA
    "Tênis": {"param": "tag_id", "id": "864"},              # ID de tag para Tênis (ATP/WTA)
    "E-sports": {"param": "tag_id", "id": "64"},            # ID de tag base para E-sports
    "NFL": {"param": "tag_id", "id": "450"}                 # ID de tag para NFL
}

# 🎯 COTAS AJUSTADAS: Mais mercados para NBA, Tênis e E-sports
LIMITES_POR_ESPORTE = {
    "NBA": 100,
    "Tênis": 150,
    "E-sports": 100,
    "NFL": 30
}

# 🎯 A MUDANÇA ESTÁ AQUI: Foco EXCLUSIVO em Moneyline (Vencedor)
# Isso resolve o problema das odds erradas.
TIPOS_PERMITIDOS = ["moneyline"]

def fetch_active_markets():
    all_markets = []
    
    for sport, dados in ESPORTES.items():
        limite = LIMITES_POR_ESPORTE.get(sport, 50)
        
        # Monta os parâmetros base
        params = {
            "active": "true",
            "closed": "false",
            "limit": limite,
            "order": "volume24hr",
            "ascending": "false",
            "sports_market_types": TIPOS_PERMITIDOS,
            "liquidity_num_min": 1000,
        }
        
        # Adiciona o parâmetro específico (series_id ou tag_id)
        params[dados["param"]] = dados["id"]
        
        # 🎯 Adiciona o filtro de "jogos" para NBA e NFL
        if sport in ["NBA", "NFL"]:
            params["tag_id"] = "100639"  # Filtra apenas jogos individuais
        
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
    
    for m in unique_markets:
        m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
        m["outcomes"] = json.loads(m.get("outcomes") or "[]")
        m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        
    return unique_markets
