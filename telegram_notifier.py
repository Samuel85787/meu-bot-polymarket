import asyncio
import html
import logging

import aiohttp
import config
from health_status import set_service

logger = logging.getLogger(__name__)

_session = None
_lock = None
_last_send_time = 0.0
MIN_INTERVAL_SECONDS = 0.25


BOT_COMMANDS = [
    {"command": "start", "description": "Ligar o monitor"},
    {"command": "stop", "description": "Desligar o monitor"},
    {"command": "ajuda", "description": "Ver todos os comandos e exemplos"},
    {"command": "status", "description": "Ver status e saúde do bot"},
    {"command": "resumo", "description": "Resumo dos alertas das últimas 24 horas"},
    {"command": "metrics", "description": "Ver métricas e resultados simulados"},
    {"command": "ranking", "description": "Ranking de desempenho por esporte"},
    {"command": "creditos", "description": "Saldo da The Odds API"},
    {"command": "sportsquota", "description": "Saldo diário das APIs NBA e NFL"},
    {"command": "nba", "description": "Analisar confronto NBA: equipe 1 vs equipe 2"},
    {"command": "nfl", "description": "Analisar confronto NFL: equipe 1 vs equipe 2"},
    {"command": "jogosnba", "description": "Agenda NBA: /jogosnba AAAA-MM-DD"},
    {"command": "jogosnfl", "description": "Agenda NFL: /jogosnfl AAAA-MM-DD"},
    {"command": "tenis", "description": "Perfil ATP/WTA de um jogador"},
    {"command": "h2h", "description": "Confronto direto entre tenistas"},
    {"command": "prejogo", "description": "Análise pré-jogo de tênis"},
    {"command": "setbet", "description": "Alterar o filtro mínimo de aposta"},
    {"command": "setspike", "description": "Alterar o filtro de oscilação"},
    {"command": "setwhale", "description": "Ligar ou desligar alertas de baleias"},
]


async def registrar_comandos_telegram():
    """Publica a lista de comandos no menu nativo do Telegram."""
    if not config.TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/setMyCommands"
    try:
        async with _session.post(url, json={"commands": BOT_COMMANDS}) as response:
            dados = await response.json(content_type=None)
            if response.status == 200 and dados.get("ok"):
                logger.info("Menu de comandos do Telegram atualizado (%s comandos)", len(BOT_COMMANDS))
                return True
            logger.warning("Telegram não aceitou a atualização do menu de comandos (HTTP %s)", response.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Não foi possível atualizar o menu do Telegram: %s", type(exc).__name__)
    return False


async def iniciar_notificador():
    global _session, _lock
    nova_sessao = _session is None or _session.closed
    if nova_sessao:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    if _lock is None:
        _lock = asyncio.Lock()
    if nova_sessao:
        await registrar_comandos_telegram()


async def fechar_notificador():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def send_alert(text: str) -> bool:
    global _last_send_time
    await iniciar_notificador()
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": html.escape(str(text)),
        "disable_web_page_preview": True,
    }
    async with _lock:
        agora = asyncio.get_running_loop().time()
        espera_minima = MIN_INTERVAL_SECONDS - (agora - _last_send_time)
        if espera_minima > 0:
            await asyncio.sleep(espera_minima)
        for tentativa in range(3):
            try:
                async with _session.post(url, json=payload) as resp:
                    dados = await resp.json(content_type=None)
                    if resp.status == 200 and dados.get("ok"):
                        _last_send_time = asyncio.get_running_loop().time()
                        set_service("telegram_sender", "ok", "último envio confirmado")
                        return True
                    if resp.status == 429:
                        espera = int(dados.get("parameters", {}).get("retry_after", 5))
                        if espera > 30:
                            logger.warning("Telegram limitou envios; mensagem não enviada.")
                            set_service("telegram_sender", "error", "limite de requisições")
                            return False
                        await asyncio.sleep(max(espera, 1))
                        continue
                    if resp.status >= 500 and tentativa < 2:
                        await asyncio.sleep(2 ** tentativa)
                        continue
                    set_service("telegram_sender", "error", f"HTTP {resp.status}")
                    logger.error("Telegram recusou mensagem: HTTP %s", resp.status)
                    return False
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                set_service("telegram_sender", "error", type(exc).__name__)
                logger.warning("Falha no envio ao Telegram: %s", type(exc).__name__)
                if tentativa < 2:
                    await asyncio.sleep(2 ** tentativa)
        set_service("telegram_sender", "error", "falha após 3 tentativas")
        return False
