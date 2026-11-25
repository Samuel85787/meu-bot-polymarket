import json
import logging
import random

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
        try:
            resultado = json.loads(valor)
            if isinstance(resultado, list):
                return resultado
        except Exception:
            pass
    return []


def normalizar_mercado(original, esporte):
    mercado = dict(original)

    if mercado.get("closed") or not mercado.get("active", True):
        return None

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


async def _get_json_with_retry(session, url, params, esporte):
    """Consulta Gamma com retry limitado para erros transitórios."""
    for tentativa in range(3):
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                retryable = resp.status == 429 or 500 <= resp.status < 600
                if not retryable or tentativa == 2:
                    logger.warning("Gamma HTTP %s para %s", resp.status, esporte)
                    return None
                retry_after = resp.headers.get("Retry-After")
                try:
                    espera = min(30.0, max(0.0, float(retry_after))) if retry_after else 2 ** tentativa
                except (TypeError, ValueError):
                    espera = 2 ** tentativa
                logger.warning("Gamma HTTP %s para %s; nova tentativa em %.1fs", resp.status, esporte, espera)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            if tentativa == 2:
                logger.warning("Falha Gamma para %s: %s", esporte, type(exc).__name__)
                return None
            espera = 2 ** tentativa
            logger.warning("Falha temporaria Gamma para %s (%s); nova tentativa em %.1fs", esporte, type(exc).__name__, espera)
        await asyncio.sleep(espera + random.uniform(0, 0.25))
    return None


async def fetch_active_markets():
    encontrados = {}
    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    consultas_ok = 0

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for esporte, tag in config.SPORT_TAGS.items():
            limite = LIMITES_POR_ESPORTE.get(esporte, 50)
            recebidos = 0

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
                    "liquidity_num_min": 500,
                }

                lote = await _get_json_with_retry(
                    session, config.GAMMA_BASE_URL + "/markets", params, esporte
                )
                if lote is None:
                    break
                if not isinstance(lote, list):
                    logger.warning("Resposta inesperada Gamma para %s", esporte)
                    break
                consultas_ok += 1

                    for original in lote:
                        if not isinstance(original, dict):
                            continue

                        try:
                            mercado = normalizar_mercado(original, esporte)
                        except (ValueError, TypeError):
                            logger.warning("Mercado invalido: %s", original.get("id"))
                            continue

                        if mercado is None:
                            continue

                        identificador = str(mercado.get("id") or mercado.get("conditionId") or "")

                        if identificador and identificador not in encontrados:
                            encontrados[identificador] = mercado
                            recebidos += 1

                        if recebidos >= limite:
                            break

                    if recebidos >= limite or len(lote) < 100:
                        break

            logger.info("%s: %s mercados", esporte, recebidos)

    if consultas_ok == 0:
        logger.error("Gamma indisponivel em todos os esportes; preservando mercados atuais")
        return None

    mercados = list(encontrados.values())[:config.MAX_MARKETS_TO_MONITOR]
    logger.info("Total: %s mercados", len(mercados))
    return mercados
