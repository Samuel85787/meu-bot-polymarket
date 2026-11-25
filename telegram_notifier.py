import asyncio
import html
import logging

import aiohttp
import config

logger = logging.getLogger(__name__)

_session = None
_lock = None
_last_send_time = 0.0
MIN_INTERVAL_SECONDS = 0.25


async def iniciar_notificador():
    global _session, _lock

    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        )
    if _lock is None:
        _lock = asyncio.Lock()


async def fechar_notificador():
    global _session

    if _session is not None and not _session.closed:
        await _session.close()

    _session = None


async def send_alert(text: str) -> bool:
    global _last_send_time
    await iniciar_notificador()

    url = (
        f"https://api.telegram.org/"
        f"bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    )

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
                        return True

                    if resp.status == 429:
                        espera = int(
                            dados.get("parameters", {}).get("retry_after", 5)
                        )

                        if espera > 30:
                            logger.warning(
                                "Telegram limitou envios; mensagem não enviada."
                            )
                            return False

                        await asyncio.sleep(max(espera, 1))
                        continue

                    if resp.status >= 500 and tentativa < 2:
                        await asyncio.sleep(2 ** tentativa)
                        continue

                    logger.error(
                        "Telegram recusou mensagem: HTTP %s",
                        resp.status,
                    )
                    return False

            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                # Não imprimir URLs de requisição: elas contêm o token.
                logger.warning(
                    "Falha no envio ao Telegram: %s",
                    type(exc).__name__,
                )

                if tentativa < 2:
                    await asyncio.sleep(2 ** tentativa)

        return False
