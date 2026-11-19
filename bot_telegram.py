import asyncio
import aiohttp
import json
import os
import logging
from typing import Optional

import config
from gamma_api import fetch_active_markets
from clob_websocket import PolymarketMonitor
from odds_checker import check_arbitrage
from sports_score_checker import conectar_sports_ws
from telegram_notifier import send_alert

# ============================================
# CONFIGURAÇÃO DE LOGGING
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================
# VARIÁVEIS GLOBAIS
# ============================================
monitor_ativo = False
task_monitor: Optional[asyncio.Task] = None
monitor_instance: Optional[PolymarketMonitor] = None
SETTINGS_FILE = "settings.json"

# ============================================
# GERENCIAMENTO DE CONFIGURAÇÕES
# ============================================
def carregar_configuracoes():
    """Carrega configurações salvas do arquivo JSON"""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                dados = json.load(f)
                config.MIN_BET_AMOUNT = dados.get("min_bet", config.MIN_BET_AMOUNT)
                config.PRICE_SPIKE_THRESHOLD = dados.get("spike", config.PRICE_SPIKE_THRESHOLD)
                config.ENABLE_WHALE_ALERTS = dados.get("whales", config.ENABLE_WHALE_ALERTS)
                logger.info("✅ Configurações salvas carregadas!")
        except Exception as e:
            logger.error(f"Erro ao carregar configurações: {e}")

def salvar_configuracoes():
    """Salva configurações atuais em arquivo JSON"""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({
                "min_bet": config.MIN_BET_AMOUNT,
                "spike": config.PRICE_SPIKE_THRESHOLD,
                "whales": config.ENABLE_WHALE_ALERTS
            }, f)
        logger.info("✅ Configurações salvas com sucesso!")
    except Exception as e:
        logger.error(f"Erro ao salvar configurações: {e}")

# ============================================
# SERVIDOR WEB (Health Check)
# ============================================
async def handle_health_check(reader, writer):
    """Handler para requisições HTTP de health check"""
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=5)
        response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nBot is alive"
        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error(f"Erro no health check: {e}")
    finally:
        writer.close()

async def iniciar_servidor_web():
    """Inicia servidor web para health checks"""
    port = int(os.environ.get("PORT", 8080))
    server = await asyncio.start_server(handle_health_check, '0.0.0.0', port)
    logger.info(f"🌐 Servidor web rodando na porta {port}")
    async with server:
        await server.serve_forever()

# ============================================
# ENVIO DE MENSAGENS (Síncrono - para Telegram Polling)
# ============================================
async def enviar_mensagem(texto: str):
    """Envia mensagem para o Telegram"""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.error(f"Erro ao enviar mensagem: {resp.status}")
    except asyncio.TimeoutError:
        logger.error("Timeout ao enviar mensagem")
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem: {e}")

# ============================================
# LISTENER DO TELEGRAM (REFATORADO PARA ASYNC)
# ============================================
async def escutar_telegram():
    """Listener assíncrono para comandos do Telegram"""
    global monitor_ativo, task_monitor, monitor_instance
    offset = 0
    logger.info("🤖 Controle do Telegram ativo. Aguardando comandos...")
    
    while True:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    if resp.status != 200:
                        logger.error(f"Erro ao buscar updates: {resp.status}")
                        await asyncio.sleep(5)
                        continue
                    
                    data = await resp.json()
                    
                    if data.get("result"):
                        for update in data["result"]:
                            offset = update["update_id"] + 1
                            
                            if "message" not in update or "text" not in update["message"]:
                                continue
                            
                            texto = update["message"]["text"].strip().lower()
                            await processar_comando(texto)
        
        except asyncio.TimeoutError:
            logger.warning("Timeout ao buscar updates do Telegram")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Erro ao escutar Telegram: {type(e).__name__}: {e}")
            await asyncio.sleep(5)

async def processar_comando(texto: str):
    """Processa comandos do Telegram"""
    global monitor_ativo, task_monitor, monitor_instance
    
    if texto in ["/start", "/iniciar"]:
        if not monitor_ativo:
            monitor_ativo = True
            task_monitor = asyncio.create_task(iniciar_monitor())
            asyncio.create_task(check_arbitrage())
            await enviar_mensagem("✅ *Monitor Ligado!* Buscando mercados e checando Odds Value...")
        else:
            await enviar_mensagem("⚠️ O monitor já está rodando!")
    
    elif texto in ["/stop", "/parar"]:
        if monitor_ativo:
            monitor_ativo = False
            if task_monitor:
                task_monitor.cancel()
            await enviar_mensagem("🛑 *Monitor Desligado.*")
        else:
            await enviar_mensagem("⚠️ O monitor já está desligado.")
    
    elif texto in ["/status", "/help", "/ajuda"]:
        await enviar_status()
    
    elif texto.startswith("/setbet"):
        await processar_setbet(texto)
    
    elif texto.startswith("/setspike"):
        await processar_setspike(texto)
    
    elif texto == "/setwhale":
        await processar_setwhale()
    
    else:
        await enviar_mensagem("Comando não reconhecido. Mande /status para ver a lista.")

async def enviar_status():
    """Envia status do bot"""
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
    await enviar_mensagem(msg)

async def processar_setbet(texto: str):
    """Processa comando /setbet"""
    try:
        valor = float(texto.split()[1])
        config.MIN_BET_AMOUNT = valor
        salvar_configuracoes()
        await enviar_mensagem(f"✅ *Filtro atualizado!* O bot agora avisa acima de ${valor:,.2f}")
    except (IndexError, ValueError):
        await enviar_mensagem("❌ Use: `/setbet 5000`")

async def processar_setspike(texto: str):
    """Processa comando /setspike"""
    try:
        valor = float(texto.split()[1])
        config.PRICE_SPIKE_THRESHOLD = valor
        salvar_configuracoes()
        if monitor_instance and hasattr(monitor_instance, 'tracker'):
            monitor_instance.tracker.threshold = valor
        await enviar_mensagem(f"✅ *Filtro atualizado!* O bot agora avisa se oscilar {valor*100:.1f}%")
    except (IndexError, ValueError):
        await enviar_mensagem("❌ Use: `/setspike 0.15`")

async def processar_setwhale():
    """Processa comando /setwhale"""
    config.ENABLE_WHALE_ALERTS = not config.ENABLE_WHALE_ALERTS
    salvar_configuracoes()
    estado_baleia = "🟢 LIGADOS" if config.ENABLE_WHALE_ALERTS else "🔴 DESLIGADOS"
    await enviar_mensagem(f"🐋 *Alertas de Baleias:* {estado_baleia}")

# ============================================
# MONITOR DE MERCADOS
# ============================================
async def iniciar_monitor():
    """Inicia o monitor de mercados"""
    global monitor_ativo, monitor_instance
    try:
        logger.info("🔎 Buscando mercados ativos...")
        markets = fetch_active_markets()
        logger.info(f"📊 {len(markets)} mercados carregados.")
        monitor_instance = PolymarketMonitor(markets)
        await monitor_instance.run()
    except asyncio.CancelledError:
        logger.info("Monitor cancelado.")
    except Exception as e:
        logger.error(f"Erro no monitor: {type(e).__name__}: {e}")
        await enviar_mensagem(f"❌ Erro no monitor: {e}")
    finally:
        monitor_ativo = False

async def loop_odds_value():
    """Verifica Odds Value a cada 6 horas"""
    while True:
        try:
            await asyncio.sleep(21600)  # 6 horas
            if monitor_ativo:
                await check_arbitrage()
        except asyncio.CancelledError:
            logger.info("Loop de Odds Value cancelado.")
            break
        except Exception as e:
            logger.error(f"Erro em loop_odds_value: {type(e).__name__}: {e}")

# ============================================
# FUNÇÃO PRINCIPAL
# ============================================
async def main():
    """Função principal do bot"""
    logger.info("🚀 Iniciando bot...")
    carregar_configuracoes()
    
    # Inicia servidor web em background
    asyncio.create_task(iniciar_servidor_web())
    
    # Inicia todas as tarefas assíncronas
    asyncio.create_task(conectar_sports_ws())
    asyncio.create_task(escutar_telegram())
    asyncio.create_task(loop_odds_value())
    
    await enviar_mensagem("🤖 Bot iniciado! Mande /start para monitorar.")
    
    # Mantém o bot rodando
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("🛑 Bot interrompido pelo usuário.")
    except Exception as e:
        logger.error(f"Erro fatal: {type(e).__name__}: {e}")

# ============================================
# ENTRADA DO PROGRAMA
# ============================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot encerrado.")
    except Exception as e:
        logger.error(f"Erro ao iniciar bot: {type(e).__name__}: {e}")