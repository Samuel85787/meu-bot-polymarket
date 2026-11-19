import asyncio
import json
import os
import time
import logging

import aiohttp

import config
from gamma_api import fetch_active_markets
from clob_websocket import PolymarketMonitor
from odds_checker import check_arbitrage
from sports_score_checker import conectar_sports_ws
from telegram_notifier import send_alert, iniciar_notificador, fechar_notificador

logger = logging.getLogger(__name__)

monitor_ativo = False
task_monitor = None
monitor_instance = None
SETTINGS_FILE = "settings.json"

def carregar_configuracoes():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                dados = json.load(f)
                config.MIN_BET_AMOUNT = float(dados.get("min_bet", config.MIN_BET_AMOUNT))
                config.PRICE_SPIKE_THRESHOLD = float(dados.get("spike", config.PRICE_SPIKE_THRESHOLD))
                config.ENABLE_WHALE_ALERTS = dados.get("whales", config.ENABLE_WHALE_ALERTS)
                logger.info("Configuracoes salvas carregadas")
        except Exception as e:
            logger.error("Erro ao carregar config: " + str(e))

def salvar_configuracoes():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({
                "min_bet": config.MIN_BET_AMOUNT,
                "spike": config.PRICE_SPIKE_THRESHOLD,
                "whales": config.ENABLE_WHALE_ALERTS
            }, f)
            logger.info("Configuracoes salvas")
    except Exception as e:
        logger.error("Erro ao salvar config: " + str(e))

async def enviar_mensagem(texto):
    url = "https://api.telegram.org/bot" + config.TELEGRAM_BOT_TOKEN + "/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.error("Erro ao enviar mensagem: " + str(resp.status))
    except Exception as e:
        logger.error("Erro ao enviar msg: " + str(e))

async def escutar_telegram():
    global monitor_ativo, task_monitor, monitor_instance
    offset = 0
    logger.info("Listener Telegram ativo")
    
    while True:
        url = "https://api.telegram.org/bot" + config.TELEGRAM_BOT_TOKEN + "/getUpdates?offset=" + str(offset) + "&timeout=30"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    if resp.status != 200:
                        logger.error("Erro getUpdates: " + str(resp.status))
                        await asyncio.sleep(5)
                        continue
                    
                    data = await resp.json()
                    
                    if data.get("result"):
                        for update in data["result"]:
                            offset = update["update_id"] + 1
                            
                            if "message" not in update or "text" not in update["message"]:
                                continue
                            
                            chat_id = str(update["message"]["chat"]["id"])
                            user_id = str(update["message"]["from"]["id"])
                            
                            if chat_id != config.TELEGRAM_CHAT_ID:
                                continue
                            
                            if config.TELEGRAM_USER_ID and user_id != config.TELEGRAM_USER_ID:
                                continue
                            
                            texto = update["message"]["text"].strip().lower()
                            await processar_comando(texto)
        
        except asyncio.TimeoutError:
            logger.warning("Timeout getUpdates")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error("Erro listener: " + str(e))
            await asyncio.sleep(5)

async def processar_comando(texto):
    global monitor_ativo, task_monitor, monitor_instance
    
    if texto in ["/start", "/iniciar"]:
        if not monitor_ativo:
            monitor_ativo = True
            task_monitor = asyncio.create_task(iniciar_monitor())
            asyncio.create_task(check_arbitrage())
            await enviar_mensagem("✅ *Monitor Ligado!*")
        else:
            await enviar_mensagem("⚠️ Monitor ja esta rodando")
    
    elif texto in ["/stop", "/parar"]:
        if monitor_ativo:
            monitor_ativo = False
            if task_monitor:
                task_monitor.cancel()
                try:
                    await task_monitor
                except asyncio.CancelledError:
                    pass
            await enviar_mensagem("🛑 *Monitor Desligado*")
        else:
            await enviar_mensagem("⚠️ Monitor ja esta desligado")
    
    elif texto in ["/status", "/help", "/ajuda"]:
        estado = "🟢 Ligado" if monitor_ativo else "🔴 Desligado"
        baleias = "🟢 Ligado" if config.ENABLE_WHALE_ALERTS else "🔴 Desligado"
        
        msg = (
            "📊 *Status do Bot:* " + estado + "\n"
            "💵 *Filtro de Aposta:* $" + str(config.MIN_BET_AMOUNT) + "\n"
            "⚡ *Filtro de Oscilacao:* " + str(int(config.PRICE_SPIKE_THRESHOLD * 100)) + "%\n"
            "🐋 *Alertas de Baleias:* " + baleias + "\n\n"
            "*Comandos:*\n"
            "/start - Ligar monitor\n"
            "/stop - Desligar monitor\n"
            "/status - Ver status\n"
            "/setbet [valor] - Mudar filtro\n"
            "/setspike [%] - Mudar oscilacao\n"
            "/setwhale - Ligar/Desligar baleias"
        )
        await enviar_mensagem(msg)
    
    elif texto.startswith("/setbet"):
        try:
            valor = float(texto.split()[1])
            config.MIN_BET_AMOUNT = valor
            salvar_configuracoes()
            await enviar_mensagem("✅ Filtro atualizado para $" + str(valor))
        except:
            await enviar_mensagem("❌ Use: /setbet 5000")
    
    elif texto.startswith("/setspike"):
        try:
            valor = float(texto.split()[1])
            config.PRICE_SPIKE_THRESHOLD = valor
            salvar_configuracoes()
            if monitor_instance and hasattr(monitor_instance, 'tracker'):
                monitor_instance.tracker.threshold = valor
            await enviar_mensagem("✅ Oscilacao atualizada para " + str(int(valor * 100)) + "%")
        except:
            await enviar_mensagem("❌ Use: /setspike 0.15")
    
    elif texto == "/setwhale":
        config.ENABLE_WHALE_ALERTS = not config.ENABLE_WHALE_ALERTS
        salvar_configuracoes()
        estado = "🟢 LIGADOS" if config.ENABLE_WHALE_ALERTS else "🔴 DESLIGADOS"
        await enviar_mensagem("🐋 *Alertas:* " + estado)
    
    else:
        await enviar_mensagem("Comando desconhecido. /status para ajuda")

async def iniciar_monitor():
    global monitor_ativo, monitor_instance
    try:
        logger.info("Buscando mercados")
        markets = await fetch_active_markets()
        logger.info("Mercados carregados: " + str(len(markets)))
        monitor_instance = PolymarketMonitor(markets)
        await monitor_instance.run()
    except asyncio.CancelledError:
        logger.info("Monitor cancelado")
    except Exception as e:
        logger.error("Erro monitor: " + str(e))
        await enviar_mensagem("❌ Erro no monitor: " + str(e))
    finally:
        monitor_ativo = False

async def loop_odds_value():
    while True:
        try:
            await asyncio.sleep(config.ODDS_CHECK_INTERVAL)
            if monitor_ativo:
                await check_arbitrage()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Erro odds: " + str(e))

async def health_check_handler(reader, writer):
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=5)
        response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nBot is alive"
        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error("Health check error: " + str(e))
    finally:
        writer.close()

async def iniciar_servidor_web():
    port = int(os.environ.get("PORT", 8080))
    server = await asyncio.start_server(health_check_handler, '0.0.0.0', port)
    logger.info("Servidor web na porta " + str(port))
    async with server:
        await server.serve_forever()

async def main():
    logger.info("Iniciando bot")
    config.validar_config()
    carregar_configuracoes()
    
    await iniciar_notificador()
    
    asyncio.create_task(iniciar_servidor_web())
    asyncio.create_task(conectar_sports_ws())
    asyncio.create_task(escutar_telegram())
    asyncio.create_task(loop_odds_value())
    
    if config.AUTO_START:
        monitor_ativo = True
        asyncio.create_task(iniciar_monitor())
        await enviar_mensagem("🤖 Bot iniciado com AUTO_START")
    else:
        await enviar_mensagem("🤖 Bot iniciado. Mande /start")
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot encerrado")
    finally:
        await fechar_notificador()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error("Erro fatal: " + str(e))