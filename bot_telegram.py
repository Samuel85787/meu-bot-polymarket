import asyncio
import json
import logging
import os

import aiohttp

import config
from clob_websocket import PolymarketMonitor
from gamma_api import fetch_active_markets
from odds_checker import check_arbitrage
from sports_score_checker import conectar_sports_ws
from telegram_notifier import fechar_notificador, iniciar_notificador, send_alert
from alert_history import initialize as inicializar_historico, get_metrics, update_resolutions
from health_status import set_service, format_status

logger = logging.getLogger(__name__)

monitor_ativo = False
task_monitor = None
monitor_instance = None
SETTINGS_FILE = config.SETTINGS_FILE


def carregar_configuracoes():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                dados = json.load(f)
                config.MIN_BET_AMOUNT = float(dados.get("min_bet", config.MIN_BET_AMOUNT))
                config.PRICE_SPIKE_THRESHOLD = float(dados.get("spike", config.PRICE_SPIKE_THRESHOLD))
                whales_value = dados.get("whales", config.ENABLE_WHALE_ALERTS)
                if isinstance(whales_value, bool):
                    config.ENABLE_WHALE_ALERTS = whales_value
                else:
                    config.ENABLE_WHALE_ALERTS = str(whales_value).strip().lower() in {
                        "true", "1", "yes", "on"
                    }
                logger.info("Configuracoes salvas carregadas")
        except Exception as e:
            logger.error("Erro ao carregar config: %s", e)


def salvar_configuracoes():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "min_bet": config.MIN_BET_AMOUNT,
                    "spike": config.PRICE_SPIKE_THRESHOLD,
                    "whales": config.ENABLE_WHALE_ALERTS,
                },
                f,
            )
            logger.info("Configuracoes salvas")
    except Exception as e:
        logger.error("Erro ao salvar config: %s", e)


async def enviar_mensagem(texto):
    await send_alert(texto)


async def escutar_telegram():
    global monitor_ativo, task_monitor, monitor_instance
    offset = 0
    logger.info("Listener Telegram ativo")

    set_service('telegram_listener', 'ok', 'iniciado')
    timeout = aiohttp.ClientTimeout(total=35)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            url = (
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
                f"?offset={offset}&timeout=30"
            )
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.error("Erro getUpdates: %s", resp.status)
                        await asyncio.sleep(5)
                        continue

                    data = await resp.json()
                    set_service('telegram_listener', 'ok', 'polling ativo' if data.get('ok') else 'resposta inesperada')

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
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                set_service("telegram_listener", "error", type(e).__name__)
                logger.error("Erro listener: %s", type(e).__name__)
                await asyncio.sleep(5)


async def processar_comando(texto):
    global monitor_ativo, task_monitor, monitor_instance

    if texto in ["/start", "/iniciar"]:
        if not monitor_ativo or task_monitor is None or task_monitor.done():
            monitor_ativo = True
            task_monitor = asyncio.create_task(iniciar_monitor())
            await enviar_mensagem("Monitor Ligado!")
        else:
            await enviar_mensagem("Monitor ja esta rodando")

    elif texto in ["/stop", "/parar"]:
        if monitor_ativo or (task_monitor is not None and not task_monitor.done()):
            monitor_ativo = False
            if task_monitor and not task_monitor.done():
                task_monitor.cancel()
                try:
                    await task_monitor
                except asyncio.CancelledError:
                    pass
            await enviar_mensagem("Monitor Desligado")
        else:
            await enviar_mensagem("Monitor ja esta desligado")

    elif texto in ["/metrics", "/metricas", "/historico"]:
        metricas = get_metrics()
        taxa = f"{metricas['win_rate']:.1f}%" if metricas["win_rate"] is not None else "Ainda sem amostra"
        ev_medio = f"{metricas['avg_ev']:+.2f}%" if metricas["avg_ev"] is not None else "—"
        linhas = [
            f"Alertas registrados: {metricas['total']}",
            f"Resultados pendentes: {metricas['pending']}",
            f"Mercados resolvidos: {metricas['settled']}",
            f"Acertos: {metricas['wins']} | Erros: {metricas['losses']}",
            f"Taxa de acerto: {taxa}",
            f"EV médio no alerta: {ev_medio}",
        ]
        for item in metricas["by_type"]:
            linhas.append(f"{item['alert_type']}: {item['total']} alertas, {item['wins'] or 0} acertos, {item['losses'] or 0} erros")
        await enviar_mensagem("HISTÓRICO E DESEMPENHO\n" + "\n".join(linhas))

    elif texto in ["/status", "/help", "/ajuda"]:
        estado = "Ligado" if monitor_ativo else "Desligado"
        diagnostico = format_status()
        baleias = "Ligado" if config.ENABLE_WHALE_ALERTS else "Desligado"
        mercados = monitor_instance.markets if monitor_instance else []
        contagem = {esporte: 0 for esporte in config.SPORT_TAGS}
        for mercado in mercados:
            esporte = mercado.get("_sport")
            if esporte in contagem:
                contagem[esporte] += 1
        resumo_esportes = "\\n".join(
            f"{esporte}: {quantidade}" for esporte, quantidade in contagem.items()
        )

        msg = (
            f"Status do Bot: {estado}\\n"
            f"Mercados monitorados: {len(mercados)}\\n"
            f"\\nSAÚDE DOS SERVIÇOS\\n{diagnostico}\\n\\n"
            f"Filtro de Aposta: ${config.MIN_BET_AMOUNT}\\n"
            f"Filtro de Oscilacao: {round(config.PRICE_SPIKE_THRESHOLD * 100, 2)}%\\n"
            f"Alertas de Baleias: {baleias}\\n"
            f"Mercados por esporte:\\n{resumo_esportes}\\n\\n"
            "Comandos:\\n"
            "/start - Ligar monitor\n"
            "/stop - Desligar monitor\n"
            "/status - Ver status\n"
            "/metrics - Ver desempenho dos alertas\n"
            "/setbet [valor] - Mudar filtro\n"
            "/setspike [ex: 0.15 ou 15] - Mudar oscilacao\n"
            "/setwhale - Ligar/Desligar baleias"
        )
        await enviar_mensagem(msg)

    elif texto.startswith("/setbet"):
        try:
            valor = float(texto.split()[1])
            if valor <= 0:
                raise ValueError()
            config.MIN_BET_AMOUNT = valor
            salvar_configuracoes()
            await enviar_mensagem(f"Filtro atualizado para ${valor}")
        except Exception:
            await enviar_mensagem("Use: /setbet 5000")

    elif texto.startswith("/setspike"):
        try:
            bruto = float(texto.split()[1])
            valor = bruto / 100.0 if bruto > 1.0 else bruto
            if valor <= 0 or valor >= 1.0:
                raise ValueError()
            config.PRICE_SPIKE_THRESHOLD = valor
            salvar_configuracoes()
            if monitor_instance and hasattr(monitor_instance, "tracker"):
                monitor_instance.tracker.threshold = valor
            await enviar_mensagem(f"Oscilacao atualizada para {round(valor * 100, 2)}%")
        except Exception:
            await enviar_mensagem("Use: /setspike 0.15 ou /setspike 15")

    elif texto == "/setwhale":
        config.ENABLE_WHALE_ALERTS = not config.ENABLE_WHALE_ALERTS
        salvar_configuracoes()
        estado = "LIGADOS" if config.ENABLE_WHALE_ALERTS else "DESLIGADOS"
        await enviar_mensagem(f"Alertas de baleia: {estado}")

    else:
        await enviar_mensagem("Comando desconhecido. /status para ajuda")


async def iniciar_monitor():
    global monitor_ativo, monitor_instance
    try:
        logger.info("Buscando mercados")
        markets = await fetch_active_markets()
        markets = markets or []
        logger.info("Mercados carregados: %s", len(markets))
        monitor_instance = PolymarketMonitor(markets)
        await monitor_instance.run()
    except asyncio.CancelledError:
        logger.info("Monitor cancelado")
    except Exception as e:
        logger.error("Erro monitor: %s", e)
        await enviar_mensagem(f"Erro no monitor: {e}")
    finally:
        monitor_ativo = False


async def loop_alert_history():
    while True:
        try:
            atualizados = await update_resolutions()
            set_service("alert_history", "ok", f"{atualizados} resultados atualizados")
            if atualizados:
                logger.info("Resultados de alertas atualizados: %s", atualizados)
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            set_service("alert_history", "error", type(exc).__name__)
            logger.warning("Erro ao atualizar histórico: %s", type(exc).__name__)
            await asyncio.sleep(60)


async def loop_odds_value():
    while True:
        try:
            if not monitor_ativo:
                await asyncio.sleep(5)
                continue
            mercados_atuais = monitor_instance.markets if monitor_instance else None
            await check_arbitrage(mercados_atuais)
            # Interrompe a espera se o monitor for desligado; ao religar,
            # a verificação volta a ocorrer sem aguardar o intervalo inteiro.
            restante = max(1, config.ODDS_CHECK_INTERVAL)
            while monitor_ativo and restante > 0:
                passo = min(5, restante)
                await asyncio.sleep(passo)
                restante -= passo
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Erro odds: %s", type(e).__name__)
            await asyncio.sleep(30)


async def supervisionar_tarefa(nome, fabrica):
    """Reinicia serviços de fundo que terminarem inesperadamente."""
    while True:
        try:
            logger.info("Iniciando tarefa supervisionada: %s", nome)
            await fabrica()
            logger.warning("Tarefa %s terminou; reiniciando em 5 segundos", nome)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Tarefa %s falhou: %s", nome, type(exc).__name__)
        await asyncio.sleep(5)


async def health_check_handler(reader, writer):
    try:
        await asyncio.wait_for(reader.readline(), timeout=5)
        response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nBot is alive"
        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error("Health check error: %s", e)
    finally:
        writer.close()


async def iniciar_servidor_web():
    port = int(os.environ.get("PORT", 8080))
    server = await asyncio.start_server(health_check_handler, "0.0.0.0", port)
    logger.info("Servidor web na porta %s", port)
    async with server:
        await server.serve_forever()


async def main():
    global monitor_ativo, task_monitor
    logger.info("Iniciando bot")
    config.validar_config()
    carregar_configuracoes()
    inicializar_historico()

    await iniciar_notificador()

    background_tasks = [
        asyncio.create_task(supervisionar_tarefa("health-server", iniciar_servidor_web)),
        asyncio.create_task(supervisionar_tarefa("sports-websocket", conectar_sports_ws)),
        asyncio.create_task(supervisionar_tarefa("telegram-listener", escutar_telegram)),
        asyncio.create_task(supervisionar_tarefa("odds-value", loop_odds_value)),
        asyncio.create_task(supervisionar_tarefa("alert-history", loop_alert_history)),
    ]

    if config.AUTO_START:
        monitor_ativo = True
        task_monitor = asyncio.create_task(iniciar_monitor())
        await enviar_mensagem("Bot iniciado com AUTO_START")
    else:
        await enviar_mensagem("Bot iniciado. Mande /start")

    try:
        await asyncio.gather(*background_tasks)
    except asyncio.CancelledError:
        raise
    finally:
        for task in background_tasks:
            task.cancel()
        if task_monitor and not task_monitor.done():
            task_monitor.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        if task_monitor:
            await asyncio.gather(task_monitor, return_exceptions=True)
        await fechar_notificador()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        asyncio.run(main())
    except Exception as e:
        logger.error("Erro fatal: %s", e)
