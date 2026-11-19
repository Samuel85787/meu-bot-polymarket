import json
import logging

import aiohttp
import config

logger = logging.getLogger(__name__)

LIMITES_POR_ESPORTE = {
    "NBA": 100,
    "NFL": 50,
    "Tênis ATP": 100,
    "Tênis WTA": 100,
    "CS2": 80,
    "VALORANT": 80,
}


def lista_json(valor):
    if isinstance(valor, list):
        return valor

    if isinstance(valor, str):
        resultado = json.loads(valor)
        if isinstance(resultado, list):
            return resultado

    return []


def normalizar_mercado(original, esporte):
    mercado = dict(original)

    if mercado.get("closed") or not mercado.get("active", True):
        return None

    # Confere também localmente, não apenas pelo filtro da API.
    tipo = str(mercado.get("sportsMarketType") or "").lower()
    if tipo != "moneyline":
        return None

    tokens = lista_json(mercado.get("clobTokenIds"))
    outcomes = lista_json(mercado.get("outcomes"))
    prices = lista_json(mercado.get("outcomePrices"))

    if not tokens or len(tokens) != len(outcomes):
        return None

    mercado["clobTokenIds"] = [str(token) for token in tokens]
    mercado["outcomes"] = [str(outcome) for outcome in outcomes]
    mercado["outcomePrices"] = prices
    mercado["_sport"] = esporte

    return mercado


async def fetch_active_markets(session):
    encontrados = {}

    for esporte, tag in config.SPORT_TAGS.items():
        limite = LIMITES_POR_ESPORTE.get(esporte, 50)
        recebidos = 0

        # Paginação limitada para evitar consultas sem fim.
        for pagina in range(5):
            params = {
                "active": "true",
                "closed": "false",
                "limit": 100,
                "offset": pagina * 100,
                "order": "volume24hr",
                "ascending": "false",
                "tag_id": tag,
                "sports_market_types": "moneyline",
                "liquidity_num_min": 1000,
            }

            try:
                async with session.get(
                    f"{config.GAMMA_BASE_URL}/markets",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Gamma: HTTP %s para %s",
                            resp.status,
                            esporte,
                        )
                        break

                    lote = await resp.json()

                if not isinstance(lote, list):
                    logger.warning("Resposta inesperada da Gamma para %s", esporte)
                    break

                for original in lote:
                    if not isinstance(original, dict):
                        continue

                    try:
                        mercado = normalizar_mercado(original, esporte)
                    except (ValueError, TypeError):
                        logger.warning(
                            "Mercado inválido ignorado: %s",
                            original.get("id"),
                        )
                        continue

                    if mercado is None:
                        continue

                    identificador = str(
                        mercado.get("id") or mercado.get("conditionId") or ""
                    )

                    if identificador and identificador not in encontrados:
                        encontrados[identificador] = mercado
                        recebidos += 1

                    if recebidos >= limite:
                        break

                if recebidos >= limite or len(lote) < 100:
                    break

            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                logger.warning(
                    "Falha ao buscar %s: %s",
                    esporte,
                    type(exc).__name__,
                )
                break

        logger.info("%s: %s mercados válidos novos", esporte, recebidos)

    mercados = list(encontrados.values())
    mercados = mercados[:config.MAX_MARKETS_TO_MONITOR]

    logger.info("Total: %s mercados selecionados", len(mercados))
    return mercados