import json
import requests
import logging
from typing import List, Dict, Any

import config

logger = logging.getLogger(__name__)

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

# 🎯 Cotas para garantir que o tênis e e-sports não sejam engolidos
LIMITES_POR_ESPORTE = {
    "NBA": 100,
    "NFL": 50,
    "Tênis ATP": 100,
    "Tênis WTA": 100,
    "CS2": 80,
    "VALORANT": 80
}

# 🎯 Foco exclusivo em Moneyline (evita odds erradas)
TIPOS_PERMITIDOS = ["moneyline"]

# 🎯 Tag de futebol para excluir (ID 100350)
TAG_FUTEBOL = "100350"

def fetch_active_markets() -> List[Dict[str, Any]]:
    """
    Busca mercados ativos de múltiplos esportes.
    
    Returns:
        List[Dict]: Lista de mercados com informações completas
    """
    all_markets = []
    
    for sport, dados in ESPORTES.items():
        limite = LIMITES_POR_ESPORTE.get(sport, 50)
        
        params = {
            "active": "true",
            "closed": "false",
            "limit": limite,
            "order": "volume24hr",
            "ascending": "false",
            "tag_id": dados["tag_id"],
            "sports_market_types": TIPOS_PERMITIDOS,
            "liquidity_num_min": 1000,  # Baixo o suficiente para pegar jogos ocultos de tênis
            "exclude_tag_id": TAG_FUTEBOL
        }
        
        try:
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=10)
            resp.raise_for_status()
            markets = resp.json()
            all_markets.extend(markets)
            logger.info(f"   [+] {len(markets)} mercados encontrados para {sport}")
        except requests.exceptions.Timeout:
            logger.error(f"   [!] Timeout ao buscar mercados para {sport}")
        except requests.exceptions.RequestException as e:
            logger.error(f"   [!] Erro ao buscar mercados para {sport}: {type(e).__name__}: {e}")

    # Remove duplicatas
    unique_markets = list({m['id']: m for m in all_markets}.values())
    logger.info(f"📊 Total de {len(unique_markets)} mercados únicos após remoção de duplicatas")
    
    # Filtro de segurança para futebol
    filtrados = []
    for m in unique_markets:
        titulo = m.get("question", "").lower()
        if "soccer" in titulo or "football" in titulo:
            continue
        filtrados.append(m)
    
    logger.info(f"📊 {len(filtrados)} mercados após filtrar futebol")
    
    # Parseia dados JSON
    for m in filtrados:
        try:
            m["clobTokenIds"] = json.loads(m.get("clobTokenIds") or "[]")
            m["outcomes"] = json.loads(m.get("outcomes") or "[]")
            m["outcomePrices"] = json.loads(m.get("outcomePrices") or "[]")
        except json.JSONDecodeError as e:
            logger.warning(f"Erro ao parsear JSON para mercado {m.get('id')}: {e}")
            continue
        
    return filtrados