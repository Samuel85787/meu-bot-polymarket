import asyncio
from telegram_notifier import send_alert

asyncio.run(send_alert("🤖 Teste do monitor! O bot está funcionando."))