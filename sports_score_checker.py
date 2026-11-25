import asyncio
import json
import logging
import time

import websockets
from health_status import set_service

logger = logging.getLogger(__name__)

SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"

placares = {}
conectado = False
CACHE_TTL = 21600


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def armazenar_update(data):
    chaves = []

    for campo in ("gameId", "slug"):
        valor = data.get(campo)
        if valor is not None and str(valor):
            chaves.append(str(valor))

    if not chaves:
        return

    if not any(campo in data for campo in ("score", "live", "ended")):
        return

    agora = time.monotonic()

    registro = {
        "score": data.get("score", ""),
        "period": data.get("period", ""),
        "elapsed": data.get("elapsed", ""),
        "live": _as_bool(data.get("live", False)),
        "ended": _as_bool(data.get("ended", False)),
        "_received": agora,
    }

    for chave in chaves:
        placares[chave] = registro

    for chave, valor in list(placares.items()):
        if agora - valor["_received"] > CACHE_TTL:
            placares.pop(chave, None)


async def conectar_sports_ws():
    global conectado

    espera = 5

    while True:
        try:
            async with websockets.connect(
                SPORTS_WS_URL,
                ping_interval=None,
                open_timeout=20,
            ) as ws:
                conectado = True
                espera = 5
                set_service("sports_ws", "ok", "conectado")
                logger.info("Feed esportivo conectado.")

                while True:
                    mensagem = await asyncio.wait_for(ws.recv(), timeout=30)

                    if isinstance(mensagem, bytes):
                        mensagem = mensagem.decode("utf-8")

                    if mensagem.strip().lower() == "ping":
                        await ws.send("pong")
                        continue

                    if mensagem.strip().lower() == "pong":
                        continue

                    try:
                        dados = json.loads(mensagem)
                    except (ValueError, TypeError):
                        continue

                    lote = dados if isinstance(dados, list) else [dados]

                    for data in lote:
                        if isinstance(data, dict):
                            armazenar_update(data)

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logger.warning(
                "Feed esportivo desconectado: %s",
                type(exc).__name__,
            )

        finally:
            conectado = False
            set_service("sports_ws", "error", "desconectado")

        await asyncio.sleep(espera)
        espera = min(espera * 2, 60)


def obter_placar(chave):
    if chave is None:
        return None

    registro = placares.get(str(chave))

    if registro is None:
        return None

    # Não mostrar placares antigos e remover entradas expiradas do cache.
    if time.monotonic() - registro["_received"] > 120:
        placares.pop(str(chave), None)
        return None

    return dict(registro)


def obter_placar_mercado(mercado):
    chaves = [mercado.get("gameId")]

    eventos = mercado.get("events") or []

    for evento in eventos:
        if isinstance(evento, dict):
            chaves.extend([
                evento.get("gameId"),
                evento.get("slug"),
            ])

    chaves.append(mercado.get("slug"))

    for chave in chaves:
        placar = obter_placar(chave)
        if placar is not None:
            return placar

    return None