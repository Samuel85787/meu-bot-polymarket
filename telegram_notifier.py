import aiohttp
import asyncio
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

async def send_alert(text: str):
    """Envia alerta para o Telegram de forma assíncrona"""
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(TELEGRAM_API, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    print("✅ Alerta enviado ao Telegram.")
                else:
                    print(f"❌ Erro ao enviar alerta: Status {resp.status}")
    except asyncio.TimeoutError:
        print("❌ Timeout ao enviar alerta para o Telegram")
    except Exception as e:
        print(f"❌ Erro ao enviar alerta: {type(e).__name__}: {e}")