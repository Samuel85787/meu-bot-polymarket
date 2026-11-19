import asyncio
import json
import websockets
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

SPORTS_WS_URL = "wss://sports-api.polymarket.com/ws"

# Cache global: mapeia o "gameId" para um dicionário com placar e status
placares: Dict[str, Dict] = {}

async def conectar_sports_ws():
    """
    Conecta ao WebSocket de esportes ao vivo e mantém o cache de placares atualizado.
    """
    while True:
        try:
            async with websockets.connect(SPORTS_WS_URL, ping_interval=20) as ws:
                logger.info("🏟️ Conectado ao feed de esportes ao vivo.")
                async for mensagem in ws:
                    try:
                        data = json.loads(mensagem)
                    except json.JSONDecodeError:
                        logger.warning("JSON inválido recebido no feed de esportes")
                        continue

                    # Processa mensagens de atualização de resultado
                    if data.get("type") == "sports_result_update":
                        await processar_update_esportes(data)
        
        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ Conexão WebSocket de esportes fechada. Reconectando em 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"⚠️ Erro na conexão de esportes: {type(e).__name__}: {e}. Reconectando em 5s...")
            await asyncio.sleep(5)

async def processar_update_esportes(data: Dict) -> None:
    """
    Processa e armazena atualizações de esportes.
    
    Args:
        data: Dados da atualização de esportes
    """
    try:
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
            logger.debug(f"📊 Placar atualizado para jogo {game_id}")
    except Exception as e:
        logger.error(f"Erro ao processar update de esportes: {type(e).__name__}: {e}")

def obter_placar(game_id: str) -> Optional[Dict]:
    """
    Retorna o placar do jogo ou None se não encontrado.
    
    Args:
        game_id: ID do jogo
    
    Returns:
        Dict com informações do placar ou None
    """
    return placares.get(game_id)