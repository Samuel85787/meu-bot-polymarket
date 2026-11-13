import asyncio
import json
import websockets
import config

SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"

# Cache global: mapeia o "gameId" para um dicionário com placar e status
placares = {}

async def conectar_sports_ws():
    """Conecta ao WebSocket de esportes e mantém o cache de placares atualizado."""
    while True:
        try:
            async with websockets.connect(SPORTS_WS_URL, ping_interval=20) as ws:
                print("🏟️ Conectado ao feed de esportes ao vivo.")
                async for mensagem in ws:
                    try:
                        data = json.loads(mensagem)
                    except json.JSONDecodeError:
                        continue

                    # A mensagem de atualização de resultado tem a estrutura:
                    # { "type": "sports_result_update", ... }
                    if data.get("type") == "sports_result_update":
                        game_id = data.get("gameId")
                        if game_id:
                            placares[game_id] = {
                                "score": data.get("score", ""),
                                "status": data.get("status", ""),
                                "live": data.get("live", False),
                                "ended": data.get("ended", False),
                                "period": data.get("period", ""),
                                "homeTeam": data.get("homeTeam", ""),
                                "awayTeam": data.get("awayTeam", ""),
                            }
        except Exception as e:
            print(f"⚠️ Conexão com feed de esportes caiu: {e}. Reconectando em 5s...")
            await asyncio.sleep(5)

def obter_placar(game_id: str):
    """Retorna o placar do jogo ou None se não encontrado."""
    return placares.get(game_id)
