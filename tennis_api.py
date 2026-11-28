"""Integração REST com AcesAPI para consultas ATP/WTA.

A chave é lida exclusivamente de ACESAPI_API_KEY (variável de ambiente).
Consultas são armazenadas em cache para preservar os créditos do plano gratuito.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
import unicodedata

import aiohttp

from acesapi_config import API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://mcp.acesapi.com/v1"
CACHE_TTL_SECONDS = 24 * 60 * 60
_cache = {}


class AcesAPIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _normalizar_nome(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").lower())
    texto = "".join(char for char in texto if not unicodedata.combining(char))
    return " ".join(texto.split())


def _cache_key(endpoint, payload):
    itens = tuple(sorted((str(k), str(v)) for k, v in payload.items()))
    return endpoint, itens


async def _request(endpoint, payload=None):
    if not API_KEY:
        raise AcesAPIError("ACESAPI_API_KEY não configurada no ambiente.")

    payload = payload or {}
    key = _cache_key(endpoint, payload)
    agora = time.monotonic()
    cached = _cache.get(key)
    if cached and agora - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    if cached:
        _cache.pop(key, None)

    timeout = aiohttp.ClientTimeout(total=20, connect=8)
    headers = {"Content-Type": "application/json", "x-api-key": API_KEY}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.post(f"{BASE_URL}/{endpoint}", json=payload) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}

                if response.status != 200:
                    messages = {
                        400: "Parâmetros inválidos enviados à AcesAPI.",
                        401: "Chave AcesAPI inválida ou sem autorização.",
                        402: "Créditos AcesAPI insuficientes.",
                        429: "Limite de requisições AcesAPI atingido. Tente mais tarde.",
                    }
                    message = messages.get(response.status, f"AcesAPI respondeu HTTP {response.status}.")
                    raise AcesAPIError(message, response.status)

                if isinstance(body, dict) and body.get("error"):
                    raise AcesAPIError(str(body.get("message") or body["error"]))

                result = body.get("data", body) if isinstance(body, dict) else body
                if not isinstance(result, dict):
                    raise AcesAPIError("Formato inesperado na resposta da AcesAPI.")

                _cache[key] = (time.monotonic(), result)
                return result
    except AcesAPIError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Falha de comunicação com AcesAPI: %s", type(exc).__name__)
        raise AcesAPIError("Não foi possível consultar a AcesAPI agora.") from exc


def _escolher_jogador(players, nome):
    if not isinstance(players, list) or not players:
        return "not_found", None

    desejado = _normalizar_nome(nome)
    exatos = [
        p for p in players
        if isinstance(p, dict) and _normalizar_nome(p.get("name")) == desejado
    ]
    if len(exatos) == 1:
        return "ok", exatos[0]
    if len(exatos) > 1:
        return "ambiguous", exatos[:5]
    if len(players) == 1 and isinstance(players[0], dict):
        return "ok", players[0]

    candidatos = [p for p in players if isinstance(p, dict)]
    return ("ambiguous", candidatos[:5]) if candidatos else ("not_found", None)


async def buscar_perfil(nome, league="tennis"):
    league = "wta" if str(league).lower() == "wta" else "tennis"
    resultado = await _request("search_players", {"name": nome, "league": league})
    estado, jogador = _escolher_jogador(resultado.get("players"), nome)
    if estado != "ok":
        return {"status": estado, "candidates": jogador or [], "league": league}

    perfil = await _request(
        "get_player",
        {"player_id": str(jogador.get("player_id")), "league": league},
    )
    perfil["status"] = "ok"
    perfil["league"] = league
    return perfil


async def buscar_confronto(nome_a, nome_b, league="tennis"):
    league = "wta" if str(league).lower() == "wta" else "tennis"
    busca_a, busca_b = await asyncio.gather(
        _request("search_players", {"name": nome_a, "league": league}),
        _request("search_players", {"name": nome_b, "league": league}),
    )
    status_a, jogador_a = _escolher_jogador(busca_a.get("players"), nome_a)
    status_b, jogador_b = _escolher_jogador(busca_b.get("players"), nome_b)

    if status_a != "ok" or status_b != "ok":
        return {
            "status": "ambiguous" if "ambiguous" in (status_a, status_b) else "not_found",
            "player_a": jogador_a,
            "player_b": jogador_b,
            "status_a": status_a,
            "status_b": status_b,
            "league": league,
        }

    if str(jogador_a.get("player_id")) == str(jogador_b.get("player_id")):
        return {"status": "same_player", "league": league}

    resultado = await _request(
        "get_head_to_head",
        {
            "player_id": str(jogador_a.get("player_id")),
            "opponent_id": str(jogador_b.get("player_id")),
            "league": league,
        },
    )
    resultado["status"] = "ok"
    resultado["league"] = league
    return resultado



SUPERFICIES = {
    "hard": "Quadra rápida",
    "clay": "Saibro",
    "grass": "Grama",
    "carpet": "Carpete",
}


def _numero_seguro(valor):
    try:
        numero = float(valor)
        return numero if numero == numero and abs(numero) != float("inf") else None
    except (TypeError, ValueError):
        return None


def probabilidade_elo(rating_a, rating_b):
    """Probabilidade teórica padrão derivada da diferença Elo, não calibrada."""
    a, b = _numero_seguro(rating_a), _numero_seguro(rating_b)
    if a is None or b is None:
        return None
    expoente = max(-20.0, min(20.0, (b - a) / 400.0))
    return 1.0 / (1.0 + 10.0 ** expoente)


def _registro_superficie(perfil, superficie):
    for item in perfil.get("by_surface") or []:
        if isinstance(item, dict) and item.get("surface") == superficie:
            return item
    return {}


def _forma_recente(perfil, quantidade=5):
    jogador = perfil.get("player") or {}
    player_id = str(jogador.get("player_id") or "")
    partidas = [
        item for item in (perfil.get("recent_matches") or [])
        if isinstance(item, dict) and item.get("winner_id")
    ]
    partidas.sort(key=lambda item: str(item.get("match_date") or ""), reverse=True)
    partidas = partidas[:quantidade]
    vitorias = sum(1 for item in partidas if str(item.get("winner_id")) == player_id)
    derrotas = len(partidas) - vitorias
    ultima = partidas[0].get("match_date") if partidas else None
    return {
        "matches": len(partidas),
        "wins": vitorias,
        "losses": derrotas,
        "win_pct": vitorias / len(partidas) if partidas else None,
        "last_match": ultima,
    }


def _data_elo(rating):
    valor = rating.get("as_of")
    if not valor:
        return None, None
    try:
        data = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)
        data = data.astimezone(timezone.utc)
        idade = max(0, (datetime.now(timezone.utc).date() - data.date()).days)
        return data.date().isoformat(), idade
    except (TypeError, ValueError):
        return None, None


async def analisar_matchup(nome_a, nome_b, league="tennis", surface=None):
    """Analisa Elo, forma, aproveitamento no piso e H2H.

    Custo sem cache: 2 buscas (1 cr cada), 2 perfis (5 cr cada) e H2H (3 cr).
    """
    league = "wta" if str(league).lower() == "wta" else "tennis"
    superficie = str(surface or "").strip().lower()
    if superficie not in {"hard", "clay", "grass", "carpet"}:
        superficie = None

    busca_a, busca_b = await asyncio.gather(
        _request("search_players", {"name": nome_a, "league": league}),
        _request("search_players", {"name": nome_b, "league": league}),
    )
    status_a, jogador_a = _escolher_jogador(busca_a.get("players"), nome_a)
    status_b, jogador_b = _escolher_jogador(busca_b.get("players"), nome_b)

    if status_a != "ok" or status_b != "ok":
        return {
            "status": "ambiguous" if "ambiguous" in (status_a, status_b) else "not_found",
            "player_a": jogador_a,
            "player_b": jogador_b,
            "status_a": status_a,
            "status_b": status_b,
            "league": league,
        }

    id_a = str(jogador_a.get("player_id") or "")
    id_b = str(jogador_b.get("player_id") or "")
    if not id_a or not id_b or id_a == id_b:
        return {"status": "same_player", "league": league}

    perfil_a, perfil_b = await asyncio.gather(
        _request("get_player", {"player_id": id_a, "league": league}),
        _request("get_player", {"player_id": id_b, "league": league}),
    )

    # Ordena os IDs para evitar duas cobranças de H2H caso o mesmo par seja
    # consultado em ordem inversa.
    primeiro, segundo = sorted((id_a, id_b))
    h2h = await _request(
        "get_head_to_head",
        {"player_id": primeiro, "opponent_id": segundo, "league": league},
    )

    rating_a = perfil_a.get("rating") or {}
    rating_b = perfil_b.get("rating") or {}
    elo_a = elo_b = None
    elo_fonte = "geral"
    amostra_a = amostra_b = None

    if superficie:
        piso_a = (rating_a.get("by_surface") or {}).get(superficie) or {}
        piso_b = (rating_b.get("by_surface") or {}).get(superficie) or {}
        valor_a = _numero_seguro(piso_a.get("rating"))
        valor_b = _numero_seguro(piso_b.get("rating"))
        amostra_a = int(piso_a.get("matches") or 0)
        amostra_b = int(piso_b.get("matches") or 0)
        # Nunca mistura Elo específico de um jogador com Elo geral do outro.
        if valor_a is not None and valor_b is not None and amostra_a >= 10 and amostra_b >= 10:
            elo_a, elo_b = valor_a, valor_b
            elo_fonte = superficie

    if elo_a is None or elo_b is None:
        elo_a = _numero_seguro(rating_a.get("overall"))
        elo_b = _numero_seguro(rating_b.get("overall"))
        elo_fonte = "geral"

    prob_a = probabilidade_elo(elo_a, elo_b)
    surface_record_a = _registro_superficie(perfil_a, superficie) if superficie else {}
    surface_record_b = _registro_superficie(perfil_b, superficie) if superficie else {}
    id_h2h_a, id_h2h_b = id_a, id_b
    summary = h2h.get("summary") or {}
    h2h_wins = summary.get("wins") or {}
    h2h_by_surface = (summary.get("by_surface") or {}).get(superficie, {}) if superficie else {}
    h2h_surface_a = int(h2h_by_surface.get(id_a, 0) or 0)
    h2h_surface_b = int(h2h_by_surface.get(id_b, 0) or 0)
    h2h_count_surface = h2h_surface_a + h2h_surface_b

    data_a, idade_a = _data_elo(rating_a)
    data_b, idade_b = _data_elo(rating_b)

    return {
        "status": "ok",
        "league": league,
        "surface": superficie,
        "surface_name": SUPERFICIES.get(superficie) if superficie else None,
        "player_a": {
            "profile": perfil_a,
            "name": (perfil_a.get("player") or {}).get("name") or jogador_a.get("name"),
            "id": id_a,
            "elo": elo_a,
            "surface_elo": _numero_seguro(((rating_a.get("by_surface") or {}).get(superficie) or {}).get("rating")) if superficie else None,
            "surface_elo_matches": amostra_a,
            "surface_record": surface_record_a,
            "recent_form": _forma_recente(perfil_a),
            "elo_date": data_a,
            "elo_age_days": idade_a,
        },
        "player_b": {
            "profile": perfil_b,
            "name": (perfil_b.get("player") or {}).get("name") or jogador_b.get("name"),
            "id": id_b,
            "elo": elo_b,
            "surface_elo": _numero_seguro(((rating_b.get("by_surface") or {}).get(superficie) or {}).get("rating")) if superficie else None,
            "surface_elo_matches": amostra_b,
            "surface_record": surface_record_b,
            "recent_form": _forma_recente(perfil_b),
            "elo_date": data_b,
            "elo_age_days": idade_b,
        },
        "elo_source": elo_fonte,
        "elo_probability_a": prob_a,
        "elo_probability_b": (1.0 - prob_a) if prob_a is not None else None,
        "h2h": {
            "total": int(summary.get("matches") or 0),
            "wins_a": int(h2h_wins.get(id_a, 0) or 0),
            "wins_b": int(h2h_wins.get(id_b, 0) or 0),
            "surface_wins_a": h2h_surface_a,
            "surface_wins_b": h2h_surface_b,
            "surface_matches": h2h_count_surface,
        },
    }


def _formatar_forma(form):
    if not form or not form.get("matches"):
        return "sem jogos recentes registrados"
    return (
        f"{form['wins']}V–{form['losses']}D nos últimos {form['matches']} "
        f"(último: {form.get('last_match') or 'data não informada'})"
    )


def formatar_analise_matchup(analise, reference_prob=None, selected_player=None):
    if analise.get("status") != "ok":
        return "Não foi possível montar a análise: jogadores não identificados de forma inequívoca."

    a, b = analise["player_a"], analise["player_b"]
    pa, pb = a["profile"], b["profile"]
    name_a, name_b = a["name"], b["name"]
    superficie = analise.get("surface_name") or "não identificada"
    linhas = [
        f"🎾 ANÁLISE PRÉ-JOGO — {name_a} x {name_b}",
        f"Circuito: {'WTA' if analise.get('league') == 'wta' else 'ATP'} | Piso: {superficie}",
    ]

    elo_a, elo_b = a.get("elo"), b.get("elo")
    if elo_a is not None and elo_b is not None:
        p = analise.get("elo_probability_a")
        fonte = "Elo por superfície" if analise.get("elo_source") != "geral" else "Elo geral"
        linhas.extend([
            f"{fonte}: {name_a} {elo_a:.0f} x {elo_b:.0f} {name_b}",
            f"Probabilidade implícita Elo: {name_a} {p * 100:.1f}% | {name_b} {(1-p) * 100:.1f}%",
        ])
        if reference_prob is not None and selected_player:
            p_sel = p if _normalizar_nome(selected_player) == _normalizar_nome(name_a) else 1 - p
            diferenca = (p_sel - reference_prob) * 100
            sinal = "+" if diferenca >= 0 else ""
            linhas.append(
                f"Diferença Elo x probabilidade sem vig da Pinnacle ({selected_player}): "
                f"{sinal}{diferenca:.1f} p.p."
            )
    else:
        linhas.append("Elo: dados insuficientes para comparar os dois jogadores.")

    if analise.get("surface"):
        linhas.append(f"Desempenho histórico em {superficie.lower()}:")
        for jogador in (a, b):
            record = jogador.get("surface_record") or {}
            wins, losses = record.get("wins"), record.get("losses")
            if wins is not None and losses is not None:
                total = int(wins) + int(losses)
                pct = float(record.get("win_pct", wins / total if total else 0)) * 100
                linhas.append(f"• {jogador['name']}: {wins}V–{losses}D ({pct:.1f}%, {total} jogos)")
            else:
                linhas.append(f"• {jogador['name']}: sem registro de carreira por piso")

    linhas.append("Forma — últimos jogos registrados:")
    linhas.append(f"• {name_a}: {_formatar_forma(a.get('recent_form'))}")
    linhas.append(f"• {name_b}: {_formatar_forma(b.get('recent_form'))}")

    h2h = analise["h2h"]
    if analise.get("surface"):
        linhas.append(
            f"H2H em {superficie.lower()}: {name_a} {h2h['surface_wins_a']} x "
            f"{h2h['surface_wins_b']} {name_b} ({h2h['surface_matches']} encontros)"
        )
        if h2h["surface_matches"] < 3:
            linhas.append("• Amostra pequena neste piso; não atribuir peso decisivo ao H2H.")
    else:
        linhas.append(
            f"H2H geral: {name_a} {h2h['wins_a']} x {h2h['wins_b']} {name_b} "
            f"({h2h['total']} encontros)"
        )

    avisos = []
    for jogador in (a, b):
        idade = jogador.get("elo_age_days")
        if idade is not None and idade > 45:
            avisos.append(
                f"Elo de {jogador['name']} sem atualização por {idade} dias "
                f"(última referência: {jogador.get('elo_date')})."
            )
    if avisos:
        linhas.append("⚠️ Atualização dos dados:")
        linhas.extend(f"• {aviso}" for aviso in avisos)

    linhas.extend([
        "",
        "Leitura estatística exploratória. A probabilidade Elo é teórica, não calibrada.",
        "A análise não altera o EV nem substitui a probabilidade sem vig da Pinnacle.",
    ])
    return "\n".join(linhas)


def formatar_perfil(perfil):
    jogador = perfil.get("player") or {}
    ranking = perfil.get("ranking") or {}
    rating = perfil.get("rating") or {}
    record = perfil.get("record") or {}
    linhas = [
        f"🎾 {jogador.get('name', 'Jogador')} — {'WTA' if perfil.get('league') == 'wta' else 'ATP'}",
        f"País: {jogador.get('country_name') or jogador.get('country') or '—'} | Mão: {jogador.get('plays') or '—'}",
        f"Ranking: #{ranking.get('rank')}" if ranking.get("rank") else "Ranking: sem dado",
        f"Elo geral: {rating.get('overall')}" if rating.get('overall') is not None else "Elo geral: sem dado",
    ]

    por_superficie = rating.get("by_surface") or {}
    nomes = {"hard": "Quadra rápida", "clay": "Saibro", "grass": "Grama"}
    superficies = []
    for chave in ("hard", "clay", "grass"):
        dados = por_superficie.get(chave) or {}
        if dados.get("rating") is not None:
            amostra = dados.get("matches")
            texto = f"{nomes[chave]} {dados['rating']}"
            if amostra is not None:
                texto += f" ({amostra} jogos)"
            superficies.append(texto)
    if superficies:
        linhas.append("Elo por superfície: " + " | ".join(superficies))

    vitorias, derrotas = record.get("wins"), record.get("losses")
    if vitorias is not None and derrotas is not None:
        aproveitamento = record.get("win_pct")
        taxa = f" ({float(aproveitamento) * 100:.1f}%)" if aproveitamento is not None else ""
        linhas.append(f"Carreira: {vitorias}V–{derrotas}D{taxa}")
    if record.get("titles") is not None:
        linhas.append(f"Títulos: {record['titles']}")

    por_superficie_record = perfil.get("by_surface") or []
    if por_superficie_record:
        linhas.append("Resultados por piso:")
        for item in por_superficie_record:
            nome = nomes.get(item.get("surface"), item.get("surface", "—"))
            w, l = item.get("wins"), item.get("losses")
            if w is not None and l is not None:
                linhas.append(f"• {nome}: {w}V–{l}D ({float(item.get('win_pct', 0)) * 100:.1f}%)")

    recentes = perfil.get("recent_matches") or []
    linhas.append("Últimas partidas registradas:")
    if recentes:
        player_id = str(jogador.get("player_id") or "")
        for jogo in recentes[:5]:
            venceu = str(jogo.get("winner_id") or "") == player_id
            adversario = (
                jogo.get("player2") if str(jogo.get("player1_id")) == player_id
                else jogo.get("player1")
            ) or "adversário não identificado"
            marcador = "V" if venceu else ("D" if jogo.get("winner_id") else "?")
            linhas.append(
                f"• {marcador} {jogo.get('match_date') or 'data —'}: "
                f"{adversario} — {jogo.get('score') or 'placar —'}"
            )
    else:
        linhas.append("• Sem partidas recentes disponíveis.")

    linhas.extend([
        "",
        "Dados estatísticos descritivos; Elo e retrospecto não são garantia nem previsão de resultado.",
        "Atualização da base: diária; não é feed ponto a ponto ao vivo.",
    ])
    return "\n".join(linhas)



def formatar_ambiguidades_h2h(resultado, nome_a, nome_b):
    linhas = ["A busca encontrou mais de uma possibilidade. Escolha usando o nome completo:"]
    for rotulo, nome, status, candidatos in (
        ("Jogador 1", nome_a, resultado.get("status_a"), resultado.get("player_a")),
        ("Jogador 2", nome_b, resultado.get("status_b"), resultado.get("player_b")),
    ):
        if status != "ambiguous":
            continue
        linhas.append(f'{rotulo} — "{nome}":')
        if isinstance(candidatos, list) and candidatos:
            for candidato in candidatos[:5]:
                if not isinstance(candidato, dict):
                    continue
                nome_candidato = candidato.get("name") or "Nome não informado"
                pais = candidato.get("country_name") or candidato.get("country")
                sufixo = f" ({pais})" if pais else ""
                linhas.append(f"• {nome_candidato}{sufixo}")
        else:
            linhas.append("• Vários resultados, sem detalhes adicionais.")
    return "\n".join(linhas)


def formatar_confronto(resultado):
    jogadores = resultado.get("players") or []
    resumo = resultado.get("summary") or {}
    if len(jogadores) < 2:
        return "A AcesAPI não retornou os dois jogadores do confronto."

    a, b = jogadores[0], jogadores[1]
    id_a, id_b = str(a.get("player_id")), str(b.get("player_id"))
    vitorias = resumo.get("wins") or {}
    sets = resumo.get("sets_won") or {}
    linhas = [
        f"🎾 H2H — {a.get('name', 'Jogador 1')} x {b.get('name', 'Jogador 2')}",
        f"Confrontos: {resumo.get('matches', 0)}",
        f"Vitórias: {a.get('name')}: {vitorias.get(id_a, 0)} | "
        f"{b.get('name')}: {vitorias.get(id_b, 0)}",
        f"Sets vencidos: {a.get('name')}: {sets.get(id_a, 0)} | "
        f"{b.get('name')}: {sets.get(id_b, 0)}",
    ]
    por_piso = resumo.get("by_surface") or {}
    if por_piso:
        linhas.append("Vitórias por superfície:")
        nomes = {"hard": "Quadra rápida", "clay": "Saibro", "grass": "Grama"}
        for chave in ("hard", "clay", "grass"):
            valores = por_piso.get(chave)
            if isinstance(valores, dict):
                linhas.append(
                    f"• {nomes[chave]}: {a.get('name')} {valores.get(id_a, 0)} x "
                    f"{valores.get(id_b, 0)} {b.get('name')}"
                )
    partidas = resultado.get("matches") or []
    if partidas:
        linhas.append("Encontros mais recentes:")
        for jogo in partidas[:5]:
            linhas.append(
                f"• {jogo.get('match_date') or 'data —'} | {jogo.get('tournament') or 'torneio —'} "
                f"({jogo.get('surface') or 'piso —'}): {jogo.get('score') or 'placar —'}"
            )
    linhas.append("H2H é contexto histórico, não previsão isolada do próximo jogo.")
    return "\n".join(linhas)
