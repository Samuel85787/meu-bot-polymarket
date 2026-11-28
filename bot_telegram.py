import asyncio
import json
import logging
import os
import re

import aiohttp

import config
from clob_websocket import PolymarketMonitor
from gamma_api import fetch_active_markets
from odds_checker import check_arbitrage, get_quota_status, atualizar_saldo_api
from sports_score_checker import conectar_sports_ws
from telegram_notifier import fechar_notificador, iniciar_notificador, send_alert
from alert_history import (initialize as inicializar_historico, get_metrics, update_resolutions, get_sport_metrics, get_extreme_alerts, replay_backtest, get_period_metrics, get_sport_ranking)
from health_status import set_service, format_status
from tennis_api import (AcesAPIError, buscar_perfil, buscar_confronto, analisar_matchup, formatar_perfil, formatar_confronto, formatar_analise_matchup, formatar_ambiguidades_h2h)
from espn_sports import (SportsDataError, analisar_confronto, formatar_analise as formatar_analise_sports, listar_jogos, formatar_jogos, formatar_status_fontes)

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

    elif texto in ["/metrics", "/metricas", "/historico", "/performance"]:
        metricas = get_metrics()
        taxa = f"{metricas['win_rate']:.1f}%" if metricas["win_rate"] is not None else "Ainda sem amostra"
        ev_medio = f"{metricas['avg_ev']:+.2f}%" if metricas["avg_ev"] is not None else "—"
        pnl = metricas["simulated_pnl_usd"]
        pnl_texto = f"${pnl:+.2f}" if pnl is not None else "Ainda sem amostra"
        roi = metricas["avg_roi_pct"]
        roi_texto = f"{roi:+.2f}%" if roi is not None else "—"
        linhas = [
            f"Alertas registrados: {metricas['total']}",
            f"Resultados pendentes: {metricas['pending']}",
            f"Mercados resolvidos: {metricas['settled']}",
            f"Acertos: {metricas['wins']} | Erros: {metricas['losses']} | Empates: {metricas['draws']}",
            f"Taxa geral (tipos de alerta misturados): {taxa}",
            f"Taxa de acerto Odds Value: {metricas['odds_win_rate']:.1f}%" if metricas["odds_win_rate"] is not None else "Taxa de acerto Odds Value: sem amostra",
            f"Liquidações 50/50 (Odds Value): {metricas['odds_draws']}",
            f"EV médio no alerta: {ev_medio}",
            "",
            "RENTABILIDADE SIMULADA (somente Odds Value)",
            f"Amostras: {metricas['pnl_samples']}",
            f"P&L hipotético: {pnl_texto}",
            f"ROI médio por alerta: {roi_texto}",
            "Estimativa teórica; não comprova execução ou lucro real.",
        ]
        calibracao = metricas["calibration"]
        if calibracao["samples"]:
            linhas.extend([
                "",
                "CALIBRAÇÃO DAS PROBABILIDADES (Odds Value)",
                f"Amostras: {calibracao['samples']}",
                f"Brier score: {calibracao['brier_score']:.4f} (menor é melhor)",
                f"Erro absoluto médio: {calibracao['mean_abs_error'] * 100:.2f} p.p.",
            ])
            for faixa in metricas["calibration_bins"]:
                linhas.append(
                    f"{faixa['probability_band']}: previsto "
                    f"{faixa['predicted'] * 100:.1f}% | observado "
                    f"{faixa['observed'] * 100:.1f}% ({faixa['samples']} casos)"
                )
        if metricas["by_sport"]:
            linhas.extend(["", "DESEMPENHO POR ESPORTE"])
            for item in metricas["by_sport"]:
                taxa_esporte = ((item["wins"] or 0) / (item["wins"] + item["losses"]) * 100
                                if (item["wins"] or 0) + (item["losses"] or 0) else None)
                taxa_txt = f"{taxa_esporte:.1f}%" if taxa_esporte is not None else "sem resultado"
                linhas.append(f"{item['sport']}: {item['total']} alertas, acerto {taxa_txt}")
        for item in metricas["by_type"]:
            linha = f"{item['alert_type']}: {item['total']} alertas, {item['wins'] or 0} acertos, {item['losses'] or 0} erros, {item['draws'] or 0} empates"
            if item["pnl_samples"]:
                linha += f", P&L simulado ${item['pnl_usd']:+.2f}, ROI médio {item['avg_roi_pct']:+.2f}%"
            linhas.append(linha)
        await enviar_mensagem("HISTÓRICO E DESEMPENHO\n" + "\n".join(linhas))

    elif texto.startswith("/backtest"):
        try:
            partes = texto.split()
            stake_teste = float(partes[1]) if len(partes) > 1 else config.ODDS_ESTIMATED_STAKE_USD
            ev_minimo = float(partes[2]) if len(partes) > 2 else None
            esporte_teste = " ".join(partes[3:]) if len(partes) > 3 else None
            resultado = replay_backtest(stake_teste, ev_minimo, esporte_teste)
            linhas = [
                "BACKTEST — REPLAY DE ALERTAS",
                f"Stake hipotética por sinal: ${stake_teste:.2f}",
                f"Amostras: {resultado['samples']}",
                f"Vitórias/derrotas/empates: {resultado['wins']}/{resultado['losses']}/{resultado['draws']}",
                f"Taxa de acerto: {resultado['win_rate']:.1f}%" if resultado["win_rate"] is not None else "Taxa de acerto: sem amostra",
                f"P&L simulado: ${resultado['total_pnl_usd']:+.2f}" if resultado["total_pnl_usd"] is not None else "P&L simulado: sem amostra",
                f"ROI médio: {resultado['avg_roi_pct']:+.2f}%" if resultado["avg_roi_pct"] is not None else "ROI médio: —",
                f"Drawdown máximo estimado: ${resultado['max_drawdown_usd']:.2f}" if resultado["max_drawdown_usd"] is not None else "Drawdown: —",
            ]
            for nome, dados in resultado["by_sport"].items():
                linhas.append(f"{nome}: {dados['samples']} sinais, P&L ${dados['pnl_usd']:+.2f}")
            linhas.extend(["", resultado["assumptions"], "Uso: /backtest [stake] [EV mínimo %] [esporte]"])
            await enviar_mensagem("\n".join(linhas))
        except (ValueError, TypeError):
            await enviar_mensagem("Uso: /backtest [stake] [EV mínimo %] [esporte]. Ex.: /backtest 100 5 NBA")

    elif texto == "/sport" or texto.startswith("/sport ") or texto == "/esporte" or texto.startswith("/esporte "):
        partes = texto.split(maxsplit=1)
        if len(partes) < 2:
            await enviar_mensagem("Use: /sport NBA | NFL | Tênis ATP | Tênis WTA | CS2 | VALORANT")
        else:
            esporte = partes[1].strip()
            dados = get_sport_metrics(esporte)
            resolvidos = (dados["wins"] or 0) + (dados["losses"] or 0)
            taxa_esporte = ((dados["wins"] or 0) / resolvidos * 100) if resolvidos else None
            linhas = [
                f"Desempenho — {esporte}",
                f"Alertas: {dados['total']}",
                f"Resolvidos: {resolvidos + (dados['draws'] or 0)}",
                f"Empates/liquidações parciais: {dados['draws'] or 0}",
                f"Taxa de acerto: {taxa_esporte:.1f}%" if taxa_esporte is not None else "Taxa de acerto: sem amostra",
                f"EV médio: {dados['avg_ev']:+.2f}%" if dados["avg_ev"] is not None else "EV médio: —",
                f"P&L simulado: ${dados['pnl_usd']:+.2f}" if dados["pnl_usd"] is not None else "P&L simulado: sem amostra",
                f"ROI médio: {dados['avg_roi_pct']:+.2f}%" if dados["avg_roi_pct"] is not None else "ROI médio: —",
            ]
            await enviar_mensagem("\\n".join(linhas))

    elif texto in ["/top", "/worst"]:
        melhores = texto == "/top"
        alertas = get_extreme_alerts(5, best=melhores)
        titulo = "MELHORES EV" if melhores else "PIORES EV"
        linhas = [titulo]
        for item in alertas:
            esporte = item["sport"] or "Esporte não identificado"
            pnl = f" | P&L ${item['pnl_usd']:+.2f}" if item["pnl_usd"] is not None else ""
            linhas.append(
                f"{esporte} — {item['outcome'] or 'Seleção'}: EV {item['ev_pct']:+.2f}%"
                f" | entrada {item['entry_price'] if item['entry_price'] is not None else '—'}{pnl}"
            )
        if not alertas:
            linhas.append("Ainda não há alertas Odds Value registrados.")
        await enviar_mensagem("\\n".join(linhas))

    elif texto == "/config":
        await enviar_mensagem(
            "CONFIGURAÇÃO ATUAL\\n"
            f"EV mínimo: {config.ODDS_EDGE_THRESHOLD * 100:.2f}%\\n"
            f"Stake simulada: ${config.ODDS_ESTIMATED_STAKE_USD:.2f}\\n"
            f"Spread máximo: {config.ODDS_MAX_SPREAD_PCT:.2f}%\\n"
            f"Slippage máximo: {config.ODDS_MAX_SLIPPAGE_PCT:.2f}%\\n"
            f"Liquidez mínima no melhor ask: ${config.ODDS_MIN_TOP_ASK_USD:.2f}\\n"
            f"Cooldown Odds: {config.ODDS_COOLDOWN_SECONDS}s\\n"
            f"Consulta de odds: a cada {config.ODDS_CHECK_INTERVAL / 3600:.1f} horas\\n"
            f"Feeds por ciclo: {config.ODDS_API_MAX_FEEDS_PER_CYCLE}\\n"
            f"Reserva de créditos: {config.ODDS_API_CREDITS_RESERVE}"
        )

    elif texto in ["/resumo", "/hoje"]:
        dados = get_period_metrics(24)
        pnl = dados["simulated_pnl_usd"]
        pnl_texto = f"${pnl:+.2f}" if pnl is not None else "sem resultados simulados"
        roi = dados["avg_roi_pct"]
        roi_texto = f"{roi:+.1f}%" if roi is not None else "—"
        mensagem = (
            "RESUMO — ÚLTIMAS 24 HORAS\n"
            f'Alertas registrados: {dados["total"]}\n'
            f'Odds Value: {dados["odds_alerts"]}\n'
            f'Apostas grandes: {dados["whale_alerts"]}\n'
            f'Oscilações de preço: {dados["spike_alerts"]}\n'
            f'Odds Value pendentes: {dados["odds_pending"]}\n'
            f'Resultados encerrados: {dados["odds_wins"]} acertos / {dados["odds_losses"]} erros / {dados["odds_draws"]} empates\\n'
            f'P&L hipotético: {pnl_texto} | ROI médio: {roi_texto}\n'
            "Valores financeiros são simulações dos alertas, não operações executadas."
        )
        await enviar_mensagem(mensagem)

    elif texto.startswith("/ranking"):
        try:
            partes = texto.split()
            dias = int(partes[1]) if len(partes) > 1 else 30
            ranking = get_sport_ranking(dias, min_samples=3)
            linhas = [f"RANKING POR ESPORTE — ÚLTIMOS {dias} DIAS",
                      "Base: alertas Odds Value resolvidos, mínimo de 3 resultados por esporte.", ""]
            if not ranking:
                linhas.append("Ainda não há amostra suficiente para montar um ranking confiável.")
            else:
                for indice, item in enumerate(ranking, 1):
                    roi = item["avg_roi_pct"]
                    pnl = item["simulated_pnl_usd"]
                    taxa = item["win_rate"]
                    linhas.append(
                        f'{indice}. {item["sport"]} — ROI médio {roi:+.1f}% | '
                        f'P&L hipotético ${pnl:+.2f} | acerto {taxa:.1f}% | {item["samples"]} resultados | {item["draws"]} empates'
                    )
            linhas.extend(["", "O ranking é retrospectivo e simulado; não representa lucro real nem garante resultados futuros."])
            await enviar_mensagem("\n".join(linhas))
        except (ValueError, TypeError):
            await enviar_mensagem("Use: /ranking ou /ranking 30")

    elif texto in ["/creditos", "/credits", "/quota"]:
        quota = await atualizar_saldo_api()
        if quota["remaining"] is None:
            mensagem = "CRÉDITOS DA API\nAinda não recebi o saldo da The Odds API. Ele será atualizado na próxima consulta gratuita de esportes."
        else:
            usados = quota["used"] if quota["used"] is not None else "—"
            restantes = quota["remaining"]
            total = quota["total"] if quota["total"] is not None else "—"
            estado = "PAUSADO para preservar a reserva" if quota["paused"] else "Ativo"
            mensagem = (
                "CRÉDITOS DA API\n"
                f"Usados: {usados} de {total}\n"
                f"Restantes: {restantes}\n"
                f"Reserva protegida: {quota['reserve']}\n"
                f"Estado das consultas: {estado}\n"
                "A consulta de esportes usada para atualizar o saldo não consome créditos."
            )
        await enviar_mensagem(mensagem)

    elif texto.startswith("/tenis"):
        partes = texto.split(maxsplit=2)
        if len(partes) < 2:
            await enviar_mensagem("Use: /tenis [atp|wta] nome do jogador. Ex.: /tenis atp sinner ou /tenis wta iga swiatek")
        else:
            liga = "tennis"
            if partes[1] in ("wta", "atp"):
                liga = "wta" if partes[1] == "wta" else "tennis"
                nome = partes[2].strip() if len(partes) > 2 else ""
            else:
                nome = " ".join(partes[1:]).strip()
            if not nome:
                await enviar_mensagem("Informe o nome do jogador. Ex.: /tenis wta iga swiatek")
            else:
                try:
                    perfil = await buscar_perfil(nome, liga)
                    if perfil.get("status") == "ambiguous":
                        candidatos = perfil.get("candidates") or []
                        linhas = ["Encontrei mais de um jogador. Informe o nome completo:"]
                        linhas.extend(
                            f"• {item.get('name')} ({item.get('country') or 'país —'})"
                            for item in candidatos
                        )
                        await enviar_mensagem("\n".join(linhas))
                    elif perfil.get("status") != "ok":
                        await enviar_mensagem(
                            f"Não encontrei {nome} no circuito selecionado. "
                            "Confira a grafia ou tente especificar ATP/WTA."
                        )
                    else:
                        await enviar_mensagem(formatar_perfil(perfil))
                except AcesAPIError as exc:
                    await enviar_mensagem(f"Consulta AcesAPI: {exc}")

    elif texto in ["/sportsquota", "/sportsquotas", "/quotasports"]:
        await enviar_mensagem(formatar_status_fontes())

    elif texto.startswith("/jogosnba") or texto.startswith("/jogosnfl"):
        sport = "NBA" if texto.startswith("/jogosnba") else "NFL"
        partes = texto.split()
        data = partes[1] if len(partes) > 1 else None
        try:
            agenda = await listar_jogos(sport, data)
            await enviar_mensagem(formatar_jogos(agenda))
        except SportsDataError as exc:
            await enviar_mensagem(f"📡 ESPN: {exc}")

    elif texto.startswith("/nba") or texto.startswith("/nfl"):
        sport = "NBA" if texto.startswith("/nba") else "NFL"
        comando = "/nba" if sport == "NBA" else "/nfl"
        argumentos = texto[len(comando):].strip()
        confronto = re.split(r"\s+(?:vs|x)\s+", argumentos, maxsplit=1)
        if len(confronto) != 2 or not all(confronto):
            await enviar_mensagem(
                f"Use: {comando} equipe da casa vs equipe visitante. "
                f"Ex.: {comando} boston celtics vs los angeles lakers"
            )
        else:
            try:
                resultado = await analisar_confronto(sport, confronto[0], confronto[1])
                if resultado.get("status") == "ok":
                    await enviar_mensagem(formatar_analise_sports(resultado))
                elif resultado.get("status") == "same_team":
                    await enviar_mensagem("As duas equipes foram identificadas como a mesma.")
                else:
                    candidatas = resultado.get("home_candidates") or []
                    extra = "\nEquipes conhecidas: " + ", ".join(candidatas[:8]) if candidatas else ""
                    await enviar_mensagem(
                        "Não consegui identificar as duas equipes. Confira os nomes." + extra
                    )
            except SportsDataError as exc:
                await enviar_mensagem(f"📡 Dados ESPN indisponíveis: {exc}")

    elif texto.startswith("/prejogo") or texto.startswith("/analise"):
        argumentos = texto.split(maxsplit=1)[1].strip() if len(texto.split(maxsplit=1)) > 1 else ""
        liga = "tennis"
        if argumentos.startswith("wta "):
            liga = "wta"
            argumentos = argumentos[4:].strip()
        elif argumentos.startswith("atp "):
            argumentos = argumentos[4:].strip()

        superficie = None
        piso_match = re.search(r"\s+(hard|hardcourt|clay|saibro|grass|grama|carpet)\s*$", argumentos)
        if piso_match:
            piso = piso_match.group(1)
            superficie = {
                "hard": "hard", "hardcourt": "hard", "clay": "clay",
                "saibro": "clay", "grass": "grass", "grama": "grass",
                "carpet": "carpet",
            }[piso]
            argumentos = argumentos[:piso_match.start()].strip()

        confronto = re.split(r"\s+(?:vs|x)\s+", argumentos, maxsplit=1)
        if len(confronto) != 2 or not all(confronto):
            await enviar_mensagem(
                "Use: /prejogo [atp|wta] jogador 1 vs jogador 2 [hard|clay|grass]. "
                "Ex.: /prejogo atp sinner vs zverev hard"
            )
        else:
            try:
                resultado = await analisar_matchup(
                    confronto[0], confronto[1], league=liga, surface=superficie
                )
                if resultado.get("status") == "ambiguous":
                    await enviar_mensagem(
                        formatar_ambiguidades_h2h(resultado, confronto[0], confronto[1])
                    )
                elif resultado.get("status") == "same_player":
                    await enviar_mensagem("Os nomes foram identificados como o mesmo jogador.")
                elif resultado.get("status") != "ok":
                    await enviar_mensagem(
                        "Não consegui identificar os dois jogadores. Use nomes completos e indique ATP ou WTA."
                    )
                else:
                    await enviar_mensagem(formatar_analise_matchup(resultado))
            except AcesAPIError as exc:
                await enviar_mensagem(f"Consulta AcesAPI: {exc}")

    elif texto.startswith("/h2h"):
        argumentos = texto[len("/h2h"):].strip()
        liga = "tennis"
        if argumentos.startswith("wta "):
            liga = "wta"
            argumentos = argumentos[4:].strip()
        elif argumentos.startswith("atp "):
            argumentos = argumentos[4:].strip()
        confronto = re.split(r"\s+(?:vs|x)\s+", argumentos, maxsplit=1)
        if len(confronto) != 2 or not all(confronto):
            await enviar_mensagem("Use: /h2h [wta] jogador 1 vs jogador 2. Ex.: /h2h sinner vs alcaraz")
        else:
            try:
                resultado = await buscar_confronto(confronto[0], confronto[1], liga)
                if resultado.get("status") == "same_player":
                    await enviar_mensagem("Os dois nomes foram identificados como o mesmo jogador.")
                elif resultado.get("status") == "not_found":
                    await enviar_mensagem("Não consegui identificar os dois jogadores. Confira os nomes e o circuito (ATP/WTA).")
                elif resultado.get("status") == "ambiguous":
                    await enviar_mensagem(formatar_ambiguidades_h2h(resultado, confronto[0], confronto[1]))
                else:
                    await enviar_mensagem(formatar_confronto(resultado))
            except AcesAPIError as exc:
                await enviar_mensagem(f"Consulta AcesAPI: {exc}")

    elif texto in ["/help", "/ajuda"]:
        msg = (
            "🤖 CENTRAL DE COMANDOS\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎛️ CONTROLE DO BOT\n"
            "/start  •  ligar o monitor\n"
            "/stop  •  pausar o monitor\n"
            "/status  •  saúde e configurações\n\n"
            "🏀 NBA  &  🏈 NFL\n"
            "/nba equipe 1 vs equipe 2\n"
            "/nfl equipe 1 vs equipe 2\n"
            "/jogosnba [AAAA-MM-DD]  •  agenda\n"
            "/jogosnfl [AAAA-MM-DD]  •  agenda\n"
            "/sportsquota  •  status da fonte de dados\n\n"
            "🎾 TÊNIS\n"
            "/tenis atp sinner  •  perfil\n"
            "/tenis wta iga swiatek  •  perfil\n"
            "/h2h sinner vs zverev  •  confronto direto\n"
            "/prejogo atp sinner vs zverev hard  •  análise\n\n"
            "📊 DESEMPENHO E HISTÓRICO\n"
            "/resumo  •  últimas 24 horas\n"
            "/metrics  •  métricas e P&L simulado\n"
            "/ranking [dias]  •  ranking por esporte\n"
            "/sport NBA  •  desempenho de um esporte\n"
            "/backtest [stake] [EV%] [esporte]\n"
            "/top  ou  /worst  •  melhores/piores EV\n\n"
            "⚙️ CONFIGURAÇÕES\n"
            "/config  •  filtros atuais\n"
            "/setbet 100  •  valor mínimo\n"
            "/setspike 15  •  oscilação mínima em %\n"
            "/setwhale  •  ligar/desligar alertas de baleias\n\n"
            "💡 DICA: use o botão Menu do Telegram para encontrar os comandos."
        )
        await enviar_mensagem(msg)

    elif texto == "/status":
        estado = "🟢 Ligado" if monitor_ativo else "⏸️ Desligado"
        diagnostico = format_status()
        baleias = "Ligado" if config.ENABLE_WHALE_ALERTS else "Desligado"
        mercados = monitor_instance.markets if monitor_instance else []
        contagem = {esporte: 0 for esporte in config.SPORT_TAGS}
        for mercado in mercados:
            esporte = mercado.get("_sport")
            if esporte in contagem:
                contagem[esporte] += 1
        resumo_esportes = "\n".join(
            f"• {esporte}: {quantidade}" for esporte, quantidade in contagem.items()
        )
        msg = (
            "🩺 STATUS DO BOT\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Monitor: {estado}\n"
            f"Mercados acompanhados: {len(mercados)}\n"
            f"Filtro mínimo: ${config.MIN_BET_AMOUNT:.2f}\n"
            f"Oscilação mínima: {config.PRICE_SPIKE_THRESHOLD * 100:.1f}%\n"
            f"Alertas de baleias: {baleias}\n\n"
            "📡 SAÚDE DOS SERVIÇOS\n"
            f"{diagnostico}\n\n"
            "🧭 MERCADOS POR ESPORTE\n"
            f"{resumo_esportes}\n\n"
            "Consulte /ajuda para ver todos os comandos."
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
        set_service("monitor", "checking", "carregando mercados")
        markets = await fetch_active_markets()
        markets = markets or []
        logger.info("Mercados carregados: %s", len(markets))
        monitor_instance = PolymarketMonitor(markets)
        set_service("monitor", "ok", f"{len(markets)} mercados carregados")
        await monitor_instance.run()
    except asyncio.CancelledError:
        set_service("monitor", "warning", "desligado pelo comando")
        logger.info("Monitor cancelado")
    except Exception as e:
        set_service("monitor", "error", type(e).__name__)
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
