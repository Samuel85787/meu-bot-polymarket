import asyncio
import threading
import time
import requests
import config
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from gamma_api import fetch_active_markets
from clob_websocket import PolymarketMonitor

monitor_ativo = False
task_monitor = None
monitor_instance = None

# Servidor HTTP simples para manter o Render acordado (Health Check)
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def iniciar_servidor_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌐 Servidor web rodando na porta {port}")
    server.serve_forever()

def enviar_mensagem(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except:
        pass

def escutar_telegram(loop):
    global monitor_ativo, task_monitor, monitor_instance
    offset = 0
    print("🤖 Controle do Telegram ativo. Aguardando comandos...")
    
    ajuda = (
        "\n\n*Comandos disponíveis:*\n"
        "▶️ /start - Ligar monitor\n"
        "🛑 /stop - Desligar monitor\n"
        "📊 /status - Ver status e filtros atuais\n"
        "💵 /setbet [valor] - Mudar filtro de aposta\n"
        "⚡ /setspike [porcentagem] - Mudar filtro de oscilação"
    )
    
    while True:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
        try:
            resp = requests.get(url, timeout=35).json()
            if resp.get("result"):
                for update in resp["result"]:
                    offset = update["update_id"] + 1
                    if "message" not in update or "text" not in update["message"]:
                        continue
                    
                    texto = update["message"]["text"].strip().lower()
                    
                    if texto == "/start" or texto == "/iniciar":
                        if not monitor_ativo:
                            monitor_ativo = True
                            task_monitor = asyncio.run_coroutine_threadsafe(iniciar_monitor(), loop)
                            enviar_mensagem("✅ *Monitor Ligado!* Buscando mercados..." + ajuda)
                        else:
                            enviar_mensagem("⚠️ O monitor já está rodando!" + ajuda)
                            
                    elif texto == "/stop" or texto == "/parar":
                        if monitor_ativo:
                            monitor_ativo = False
                            if task_monitor:
                                task_monitor.cancel()
                            enviar_mensagem("🛑 *Monitor Desligado.*")
                        else:
                            enviar_mensagem("⚠️ O monitor já está desligado.")
                            
                    elif texto == "/status" or texto == "/help" or texto == "/ajuda":
                        estado = "🟢 Ligado" if monitor_ativo else "🔴 Desligado"
                        msg = (
                            f"📊 *Status do Bot:* {estado}\n"
                            f"💵 *Filtro de Aposta:* ${config.MIN_BET_AMOUNT:,.2f}\n"
                            f"⚡ *Filtro de Oscilação:* {config.PRICE_SPIKE_THRESHOLD*100:.1f}%"
                            + ajuda
                        )
                        enviar_mensagem(msg)

                    elif texto.startswith("/setbet"):
                        try:
                            valor = float(texto.split()[1])
                            config.MIN_BET_AMOUNT = valor
                            enviar_mensagem(f"✅ *Filtro atualizado!* O bot agora avisa acima de ${valor:,.2f}")
                        except:
                            enviar_mensagem("❌ Use: `/setbet 5000` (para definir o valor em dólares)")

                    elif texto.startswith("/setspike"):
                        try:
                            valor = float(texto.split()[1])
                            config.PRICE_SPIKE_THRESHOLD = valor
                            if monitor_instance and hasattr(monitor_instance, 'tracker'):
                                monitor_instance.tracker.threshold = valor
                            enviar_mensagem(f"✅ *Filtro atualizado!* O bot agora avisa se oscilar {valor*100:.1f}%")
                        except:
                            enviar_mensagem("❌ Use: `/setspike 0.15` (ex: 0.15 = 15%)")

                    else:
                        enviar_mensagem("Comando não reconhecido." + ajuda)
        except Exception:
            time.sleep(5)

async def iniciar_monitor():
    global monitor_ativo, monitor_instance
    try:
        print("🔎 Buscando mercados ativos...")
        markets = fetch_active_markets()
        print(f"📊 {len(markets)} mercados carregados.")
        monitor_instance = PolymarketMonitor(markets)
        await monitor_instance.run()
    except asyncio.CancelledError:
        print("Monitor cancelado pelo usuário.")
    except Exception as e:
        enviar_mensagem(f"❌ Erro no monitor: {e}")
    finally:
        monitor_ativo = False
        print("Monitor parou de rodar.")

async def main():
    # Inicia o servidor HTTP em uma thread separada
    thread_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
    thread_web.start()

    loop = asyncio.get_running_loop()
    thread_telegram = threading.Thread(target=escutar_telegram, args=(loop,), daemon=True)
    thread_telegram.start()
    enviar_mensagem("🤖 Bot iniciado! Digite /start para começar a monitorar.")
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
