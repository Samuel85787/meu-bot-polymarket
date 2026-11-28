import asyncio
import html
import logging

import aiohttp
import config
from health_status import set_service
from telegram_ui import keyboard_url, render_message, split_message

logger = logging.getLogger(__name__)

_session = None
_lock = None
_last_send_time = 0.0
MIN_INTERVAL_SECONDS = 0.25


BOT_COMMANDS = [
    {"command": "start", "description": "Ligar o monitor de mercados"},
    {"command": "stop", "description": "Pausar o monitor"},
    {"command": "ajuda", "description": "Abrir o guia de comandos"},
    {"command": "status", "description": "Ver saúde e configurações do bot"},
    {"command": "nba", "description": "Analisar confronto NBA (equipe 1 vs equipe 2)"},
    {"command": "nfl", "description": "Analisar confronto NFL (equipe 1 vs equipe 2)"},
    {"command": "jogosnba", "description": "Ver agenda atual ou por data da NBA"},
    {"command": "jogosnfl", "description": "Ver agenda atual ou por data da NFL"},
    {"command": "sportsquota", "description": "Ver status das fontes NBA e NFL"},
    {"command": "tenis", "description": "Consultar perfil de tenista ATP ou WTA"},
    {"command": "h2h", "description": "Comparar o histórico de dois tenistas"},
    {"command": "prejogo", "description": "Análise pré-jogo de tênis por superfície"},
    {"command": "resumo", "description": "Resumo dos alertas das últimas 24 horas"},
    {"command": "metrics", "description": "Métricas e resultados simulados"},
    {"command": "ranking", "description": "Ranking de desempenho por esporte"},
    {"command": "sport", "description": "Desempenho histórico de um esporte"},
    {"command": "backtest", "description": "Reproduzir resultados dos alertas registrados"},
    {"command": "creditos", "description": "Consultar créditos da The Odds API"},
    {"command": "config", "description": "Ver os filtros atuais de análise"},
    {"command": "top", "description": "Ver os cinco maiores EV registrados"},
    {"command": "worst", "description": "Ver os cinco menores EV registrados"},
    {"command": "setbet", "description": "Alterar o valor mínimo de aposta"},
    {"command": "setspike", "description": "Alterar o filtro de oscilação"},
    {"command": "setwhale", "description": "Ativar ou pausar alertas de baleias"},
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


async def send_alert(
    text: str,
    button_url: str | None = None,
    button_label: str = "Abrir mercado",
) -> bool:
    """Send consistently styled, HTML-safe Telegram messages."""
    global _last_send_time
    await iniciar_notificador()
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    raw_chunks = split_message(text)
    async with _lock:
        for index, chunk in enumerate(raw_chunks):
            # render_message escapes every dynamic character before adding trusted tags.
            rendered = render_message(chunk, first_chunk=(index == 0))
            payload = {
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": rendered,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if button_url and index == len(raw_chunks) - 1:
                markup = keyboard_url(button_url, button_label)
                if markup:
                    payload["reply_markup"] = markup

            enviado = False
            for tentativa in range(3):
                try:
                    agora = asyncio.get_running_loop().time()
                    espera_minima = MIN_INTERVAL_SECONDS - (agora - _last_send_time)
                    if espera_minima > 0:
                        await asyncio.sleep(espera_minima)
                    async with _session.post(url, json=payload) as resp:
                        dados = await resp.json(content_type=None)
                        if resp.status == 200 and dados.get("ok"):
                            _last_send_time = asyncio.get_running_loop().time()
                            set_service("telegram_sender", "ok", "último envio confirmado")
                            enviado = True
                            break
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
            if not enviado:
                set_service("telegram_sender", "error", "falha após 3 tentativas")
                return False
    return True
