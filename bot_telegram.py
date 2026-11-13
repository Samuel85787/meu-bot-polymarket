import asyncio
import threading
import time
import requests
import config
import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from gamma_api import fetch_active_markets
from clob_websocket import PolymarketMonitor
from odds_checker import check_arbitrage
from sports_score_checker import conectar_sports_ws  # <-- Nova importação

monitor_ativo = False
task_monitor = None
monitor_instance = None

SETTINGS_FILE = "settings.json"

def carregar_configuracoes():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                dados = json.load(f)
                config.MIN_BET_AMOUNT = dados.get("min_bet", config.MIN_BET_AMOUNT)
                config.PRICE_SPIKE_THRESHOLD = dados.get("spike", config.PRICE_SPIKE_THRESHOLD)
                config.ENABLE_WHALE_ALERTS = dados.get("whales", config.ENABLE_WHALE_ALERTS)
                print("✅ Configurações salvas carregadas!")
        except Exception as e:
            print(f"Erro ao carregar configurações: {e}")

def salvar_configuracoes():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({
                "min_bet": config.MIN_BET_AMOUNT,
                "spike": config.PRICE_SPIKE_THRESHOLD,
                "whales": config.ENABLE_WHALE_ALERTS
            }, f)
    except Exception as e:
        print(f"Erro ao salvar configurações: {e}")

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
                            asyncio.run_coroutine_threadsafe(check_arbitrage(), loop)
                            enviar_mensagem("✅ *Monitor Ligado!* Buscando mercados e checando Odds Value...")
                        else:
                            enviar_mensagem("⚠️ O monitor já está rodando!")
                            
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
                        baleias = "🟢 Ligado" if config.ENABLE_WHALE_ALERTS else "🔴 Desligado"
                        
                        msg = (
                            f"📊 *Status do Bot:* {estado}\n"
                            f"💵 *Filtro de Aposta:* ${config.MIN_BET_AMOUNT:,.2f}\n"
                            f"⚡ *Filtro de Oscilação:* {config.PRICE_SPIKE_THRESHOLD*100:.1f}%\n"
                            f"🐋 *Alertas de Baleias:* {baleias}\n\n"
                            f"*Comandos disponíveis:*\n"
                            f"▶️ /start - Ligar monitor\n"
                            f"🛑 /stop - Desligar monitor\n"
                            f"📊 /status - Ver status atual\n"
                            f"💵 /setbet [valor] - Mudar filtro de aposta\n"
                            f"⚡ /setspike [%] - Mudar filtro de oscilação\n"
                            f"🐋 /setwhale - Ligar/Desligar alertas de baleias"
                        )
                        enviar_mensagem(msg)

                    elif texto.startswith("/setbet"):
                        try:
                            valor = float(texto.split()[1])
                            config.MIN_BET_AMOUNT = valor
                            salvar_configuracoes()
                            enviar_mensagem(f"✅ *Filtro atualizado!* O bot agora avisa acima de ${valor:,.2f}")
                        except:
                            enviar_mensagem("❌ Use: `/setbet 5000`")

                    elif texto.startswith("/setspike"):
                        try:
                            valor = float(texto.split()[1])
                            config.PRICE_SPIKE_THRESHOLD = valor
                            salvar_configuracoes()
                            if monitor_instance and hasattr(monitor_instance, 'tracker'):
                                monitor_instance.tracker.threshold = valor
                            enviar_mensagem(f"✅ *Filtro atualizado!* O bot agora avisa se oscilar {valor*100:.1f}%")
                        except:
                            enviar_mensagem("❌ Use: `/setspike 0.15`")

                    elif texto == "/setwhale":
                        config.ENABLE_WHALE_ALERTS = not config.ENABLE_WHALE_ALERTS
                        salvar_configuracoes()
                        estado_baleia = "🟢 LIGADOS" if config.ENABLE_WHALE_ALERTS else "🔴 DESLIGADOS"
                        enviar_mensagem(f"🐋 *Alertas de Baleias:* {estado_baleia}")

                    else:
                        enviar_mensagem("Comando não reconhecido. Mande /status para ver a lista.")
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
        print("Monitor cancelado.")
    except Exception as e:
        enviar_mensagem(f"❌ Erro no monitor: {e}")
    finally:
        monitor_ativo = False

async def loop_odds_value():
    """Roda a verificação de Odds Value a cada 6 horas"""
    while True:
        await asyncio.sleep(21600) 
        if monitor_ativo:
            await check_arbitrage()

async def main():
    carregar_configuracoes()
    thread_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
    thread_web.start()

    # 🆕 Inicia o feed de placares da Polymarket
    asyncio.create_task(conectar_sports_ws())

    loop = asyncio.get_running_loop()
    thread_telegram = threading.Thread(target=escutar_telegram, args=(loop,), daemon=True)
    thread_telegram.start()
    
    asyncio.create_task(loop_odds_value()) 
    
    enviar_mensagem("🤖 Bot iniciado! Mande /start para monitorar.")
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
