"""Integração REST com AcesAPI para consultas ATP/WTA.

A chave é lida exclusivamente de ACESAPI_API_KEY (variável de ambiente).
Consultas são armazenadas em cache para preservar os créditos do plano gratuito.
"""
import asyncio
import logging
import time
import unicodedata

import aiohttp

from acesapi_config import API_KEY

logger = logging.getLogger(__name__)

BASE_URL = "https://mcp.acesapi.com/v1"
CACHE_TTL_SECONDS = 6 * 60 * 60
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
