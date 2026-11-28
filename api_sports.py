"""Cliente API-SPORTS para contexto pré-jogo de NBA e NFL.

Chave lida de API_SPORTS_KEY. Nunca registre a chave em logs.
As respostas são armazenadas em cache para respeitar a franquia de 100 req/dia.
"""
import asyncio
import logging
import os
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_SPORTS_KEY", "").strip()
NBA_BASE = "https://v2.nba.api-sports.io"
NFL_BASE = "https://v1.american-football.api-sports.io"
TIMEZONE = "America/Recife"
NBA_LEAGUE = "standard"
NBA_SEASON = 2026
NFL_SEASON = 2026
CACHE_SECONDS = 6 * 60 * 60
TEAMS_CACHE_SECONDS = 24 * 60 * 60
RESERVE_REQUESTS = 10

_cache = {}
_quota = {
    "NBA": {"limit": None, "remaining": None, "used": None, "updated_at": None},
    "NFL": {"limit": None, "remaining": None, "used": None, "updated_at": None},
}
_analysis_cache = {}


class SportsAPIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _norm(value):
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def _cache_key(sport, endpoint, params):
    return sport, endpoint, tuple(sorted((str(k), str(v)) for k, v in params.items()))


def _response_list(payload):
    if isinstance(payload, dict):
        result = payload.get("response")
        return result if isinstance(result, list) else []
    return payload if isinstance(payload, list) else []


def _response_object(payload):
    if isinstance(payload, dict):
        response = payload.get("response")
        return response if isinstance(response, dict) else payload
    return {}


def _update_quota(sport, headers):
    quota = _quota[sport]
    for header, field in (
        ("x-ratelimit-requests-limit", "limit"),
        ("x-ratelimit-requests-remaining", "remaining"),
    ):
        try:
            value = headers.get(header)
            if value is not None:
                quota[field] = int(value)
        except (TypeError, ValueError):
            pass
    if quota["limit"] is not None and quota["remaining"] is not None:
        quota["used"] = max(0, quota["limit"] - quota["remaining"])
    quota["updated_at"] = time.time()


async def _request(sport, endpoint, params=None, ttl=CACHE_SECONDS, free_status=False):
    if not API_KEY:
        raise SportsAPIError("API_SPORTS_KEY não configurada no ambiente.")

    sport = sport.upper()
    if sport not in ("NBA", "NFL"):
        raise SportsAPIError("Esporte não suportado pela API-SPORTS.")

    params = params or {}
    key = _cache_key(sport, endpoint, params)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < ttl:
        return cached[1]
    if cached:
        _cache.pop(key, None)

    quota = _quota[sport]
    if not free_status and quota["remaining"] is not None:
        if quota["remaining"] <= RESERVE_REQUESTS:
            raise SportsAPIError(
                f"Consulta {sport} pausada para preservar a reserva "
                f"({quota['remaining']} requisições restantes)."
            )

    base = NBA_BASE if sport == "NBA" else NFL_BASE
    timeout = aiohttp.ClientTimeout(total=20, connect=8)
    headers = {"x-apisports-key": API_KEY}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{base}/{endpoint}", params=params, headers=headers
            ) as response:
                _update_quota(sport, response.headers)
                try:
                    body = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}

                if response.status != 200:
                    messages = {
                        401: "API key recusada pela API-SPORTS.",
                        403: "A API-SPORTS não liberou este endpoint para a assinatura.",
                        404: "Endpoint ou recurso não encontrado na API-SPORTS.",
                        429: "Limite de requisições da API-SPORTS atingido.",
                    }
                    raise SportsAPIError(
                        messages.get(response.status, f"API-SPORTS respondeu HTTP {response.status}."),
                        response.status,
                    )

                errors = body.get("errors") if isinstance(body, dict) else None
                if errors:
                    if isinstance(errors, dict):
                        detalhe = "; ".join(f"{k}: {v}" for k, v in errors.items())
                    elif isinstance(errors, list):
                        detalhe = "; ".join(str(item) for item in errors)
                    else:
                        detalhe = str(errors)
                    raise SportsAPIError(f"API-SPORTS: {detalhe}")

                _cache[key] = (time.monotonic(), body)
                return body
    except SportsAPIError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Falha de comunicação API-SPORTS (%s): %s", sport, type(exc).__name__)
        raise SportsAPIError(f"Não foi possível consultar a API-SPORTS ({sport}) agora.") from exc


async def obter_cotas():
    """O endpoint /status não consome requisições diárias, segundo a documentação."""
    resultados = {}
    for sport in ("NBA", "NFL"):
        try:
            body = await _request(sport, "status", ttl=300, free_status=True)
            data = _response_object(body)
            requests = data.get("requests") or {}
            account = data.get("account") or {}
            subscription = data.get("subscription") or {}
            limit = requests.get("limit_day")
            used = requests.get("current")
            remaining = None
            if limit is not None and used is not None:
                remaining = max(0, int(limit) - int(used))
            _quota[sport].update({
                "limit": int(limit) if limit is not None else _quota[sport]["limit"],
                "used": int(used) if used is not None else _quota[sport]["used"],
                "remaining": remaining if remaining is not None else _quota[sport]["remaining"],
                "updated_at": time.time(),
            })
            resultados[sport] = {
                **_quota[sport],
                "plan": subscription.get("plan"),
                "active": subscription.get("active"),
                "account": account.get("email"),
            }
        except SportsAPIError as exc:
            resultados[sport] = {**_quota[sport], "error": str(exc)}
    return resultados


def formatar_cotas(cotas):
    linhas = ["API-SPORTS — CONSUMO DIÁRIO"]
    for sport in ("NBA", "NFL"):
        item = cotas.get(sport) or {}
        if item.get("error"):
            linhas.append(f"{sport}: {item['error']}")
            continue
        restante, limite, usados = item.get("remaining"), item.get("limit"), item.get("used")
        if restante is None:
            linhas.append(f"{sport}: saldo ainda não informado pelo serviço.")
        else:
            linhas.append(f"{sport}: {restante}/{limite} requisições restantes ({usados} usadas).")
        if item.get("plan"):
            linhas.append(f"Plano: {item['plan']} | Ativo: {item.get('active')}")
    linhas.append("A consulta de status é gratuita; reservas de 10 requisições são protegidas.")
    return "\n".join(linhas)


def _find_nfl_league(response):
    for league in _response_list(response):
        if not isinstance(league, dict):
            continue
        nome = _norm(league.get("name"))
        if nome == "nfl":
            return league
    return None


async def _nfl_league():
    body = await _request("NFL", "leagues", {"season": NFL_SEASON}, ttl=TEAMS_CACHE_SECONDS)
    league = _find_nfl_league(body)
    if not league or league.get("id") is None:
        raise SportsAPIError(
            "Não encontrei a liga NFL na temporada 2026. Verifique a cobertura do plano."
        )
    return league


async def _teams(sport):
    if sport == "NBA":
        body = await _request(
            "NBA", "teams", {"league": NBA_LEAGUE}, ttl=TEAMS_CACHE_SECONDS
        )
    else:
        league = await _nfl_league()
        body = await _request(
            "NFL", "teams",
            {"league": league["id"], "season": NFL_SEASON},
            ttl=TEAMS_CACHE_SECONDS,
        )
    return _response_list(body)


def _resolve_team(teams, name):
    target = _norm(name)
    if not target:
        return None
    for team in teams:
        if not isinstance(team, dict):
            continue
        aliases = {
            _norm(team.get("name")),
            _norm(team.get("nickname")),
            _norm(team.get("code")),
        }
        aliases.discard("")
        if target in aliases:
            return team
    # Aceita apelido ou parte exata do nome apenas quando a correspondência é única.
    matches = []
    for team in teams:
        if not isinstance(team, dict):
            continue
        aliases = [_norm(team.get(field)) for field in ("name", "nickname", "code")]
        if any(alias and (target in alias.split() or alias in target.split()) for alias in aliases):
            matches.append(team)
    unique = {str(team.get("id")): team for team in matches if team.get("id") is not None}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _game_teams(game):
    teams = game.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("visitors") or teams.get("away") or {}
    return home, away


def _game_date(game):
    date = game.get("date")
    if isinstance(date, dict):
        return str(date.get("start") or date.get("date") or "")
    return str(date or game.get("game_date") or "")


def _is_finished(game):
    status = game.get("status") or {}
    if isinstance(status, dict):
        short = str(status.get("short") or "").upper()
        long = str(status.get("long") or "").lower()
    else:
        short, long = str(status).upper(), ""
    return short in {"3", "FT", "AOT", "FINAL", "FINISHED"} or "finished" in long or "final" in long


def _score_value(value):
    if isinstance(value, dict):
        for field in ("points", "total", "score"):
            if value.get(field) is not None:
                return _number(value.get(field))
    return _number(value)


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _game_scores(game):
    scores = game.get("scores") or {}
    return _score_value(scores.get("home")), _score_value(
        scores.get("visitors") if scores.get("visitors") is not None else scores.get("away")
    )


def _team_record(games, team_id):
    team_id = str(team_id)
    finished = []
    for game in games:
        if not isinstance(game, dict) or not _is_finished(game):
            continue
        home, away = _game_teams(game)
        home_id, away_id = str(home.get("id") or ""), str(away.get("id") or "")
        if team_id not in (home_id, away_id):
            continue
        home_score, away_score = _game_scores(game)
        if home_score is None or away_score is None or home_score == away_score:
            continue
        is_home = home_id == team_id
        points_for, points_against = (
            (home_score, away_score) if is_home else (away_score, home_score)
        )
        finished.append({
            "date": _game_date(game),
            "win": points_for > points_against,
            "points_for": points_for,
            "points_against": points_against,
            "opponent": (away if is_home else home).get("name"),
            "score": f"{int(points_for)}-{int(points_against)}",
        })
    finished.sort(key=lambda item: item["date"], reverse=True)
    wins = sum(1 for item in finished if item["win"])
    losses = len(finished) - wins
    last_five = finished[:5]
    return {
        "games": len(finished),
        "wins": wins,
        "losses": losses,
        "win_pct": wins / len(finished) if finished else None,
        "last_five": last_five,
        "last_five_wins": sum(1 for item in last_five if item["win"]),
        "last_five_losses": sum(1 for item in last_five if not item["win"]),
        "avg_for": (sum(x["points_for"] for x in finished) / len(finished)) if finished else None,
        "avg_against": (sum(x["points_against"] for x in finished) / len(finished)) if finished else None,
    }


def _h2h_record(games, team_a_id, team_b_id):
    team_a_id, team_b_id = str(team_a_id), str(team_b_id)
    jogos = []
    for game in games:
        if not isinstance(game, dict) or not _is_finished(game):
            continue
        home, away = _game_teams(game)
        ids = {str(home.get("id") or ""), str(away.get("id") or "")}
        if ids != {team_a_id, team_b_id}:
            continue
        home_score, away_score = _game_scores(game)
        if home_score is None or away_score is None or home_score == away_score:
            continue
        winner = str(home.get("id")) if home_score > away_score else str(away.get("id"))
        jogos.append({
            "date": _game_date(game),
            "winner": winner,
            "home": home.get("name"),
            "away": away.get("name"),
            "score": f"{int(home_score)}-{int(away_score)}",
        })
    jogos.sort(key=lambda item: item["date"], reverse=True)
    return {
        "total": len(jogos),
        "wins_a": sum(1 for item in jogos if item["winner"] == team_a_id),
        "wins_b": sum(1 for item in jogos if item["winner"] == team_b_id),
        "recent": jogos[:5],
    }


async def analisar_confronto(sport, home_name, away_name):
    sport = sport.upper()
    if sport not in ("NBA", "NFL"):
        raise SportsAPIError("Use NBA ou NFL.")

    teams = await _teams(sport)
    home_team = _resolve_team(teams, home_name)
    away_team = _resolve_team(teams, away_name)
    if not home_team or not away_team:
        return {
            "status": "not_found",
            "sport": sport,
            "home_candidates": [x.get("name") for x in teams if isinstance(x, dict)][:10],
        }

    home_id, away_id = str(home_team.get("id")), str(away_team.get("id"))
    if home_id == away_id:
        return {"status": "same_team", "sport": sport}

    season = NBA_SEASON if sport == "NBA" else NFL_SEASON
    pair = tuple(sorted((home_id, away_id)))
    analysis_key = (sport, season, pair)
    now = time.monotonic()
    cached = _analysis_cache.get(analysis_key)
    if cached and now - cached[0] < CACHE_SECONDS:
        # Mantém a ordem de apresentação pedida pelo usuário.
        result = dict(cached[1])
        if result["home"]["id"] != home_id:
            result["home"], result["away"] = result["away"], result["home"]
            result["h2h"]["wins_home"], result["h2h"]["wins_away"] = (
                result["h2h"]["wins_away"], result["h2h"]["wins_home"]
            )
        return result

    if sport == "NBA":
        params_home = {"team": home_id, "league": NBA_LEAGUE, "season": season}
        params_away = {"team": away_id, "league": NBA_LEAGUE, "season": season}
        params_h2h = {"h2h": f"{home_id}-{away_id}"}
    else:
        league = await _nfl_league()
        params_home = {"team": home_id, "league": league["id"], "season": season}
        params_away = {"team": away_id, "league": league["id"], "season": season}
        params_h2h = {"h2h": f"{home_id}-{away_id}", "league": league["id"], "season": season}

    home_body, away_body, h2h_body = await asyncio.gather(
        _request(sport, "games", params_home, ttl=CACHE_SECONDS),
        _request(sport, "games", params_away, ttl=CACHE_SECONDS),
        _request(sport, "games", params_h2h, ttl=TEAMS_CACHE_SECONDS),
    )

    resultado = {
        "status": "ok",
        "sport": sport,
        "season": season,
        "home": {
            "id": home_id,
            "name": home_team.get("name") or home_name,
            "record": _team_record(_response_list(home_body), home_id),
        },
        "away": {
            "id": away_id,
            "name": away_team.get("name") or away_name,
            "record": _team_record(_response_list(away_body), away_id),
        },
        "h2h": _h2h_record(_response_list(h2h_body), home_id, away_id),
    }
    _analysis_cache[analysis_key] = (time.monotonic(), resultado)
    return resultado


def _form_text(record):
    last = record.get("last_five") or []
    if not last:
        return "sem partidas concluídas no histórico retornado"
    forma = " ".join("V" if item["win"] else "D" for item in last)
    return (
        f"{record['wins']}V–{record['losses']}D na temporada; "
        f"últimos {len(last)}: {record['last_five_wins']}V–{record['last_five_losses']}D "
        f"({forma})"
    )


def formatar_analise(analise):
    if analise.get("status") == "same_team":
        return "Os dois nomes foram identificados como a mesma equipe."
    if analise.get("status") != "ok":
        candidatos = analise.get("home_candidates") or []
        detalhe = "\nEquipes disponíveis: " + ", ".join(candidatos[:8]) if candidatos else ""
        return "Não consegui identificar uma das equipes com segurança." + detalhe

    home, away = analise["home"], analise["away"]
    linhas = [
        f"📊 CONTEXTO PRÉ-JOGO — {home['name']} x {away['name']}",
        f"{analise['sport']} | Temporada {analise['season']}",
        "",
        "Campanha e forma recente:",
        f"• {home['name']}: {_form_text(home['record'])}",
        f"• {away['name']}: {_form_text(away['record'])}",
    ]
    for label, team in (("Mandante", home), ("Visitante", away)):
        record = team["record"]
        if record.get("avg_for") is not None and record.get("avg_against") is not None:
            linhas.append(
                f"• {label} — média: {record['avg_for']:.1f} pontos feitos / "
                f"{record['avg_against']:.1f} sofridos por jogo"
            )

    h2h = analise["h2h"]
    linhas.extend([
        "",
        f"Confronto direto na base retornada: {home['name']} {h2h['wins_home']} x "
        f"{h2h['wins_away']} {away['name']} ({h2h['total']} partidas)",
    ])
    if h2h["total"] < 3:
        linhas.append("• H2H com amostra pequena; não deve receber peso decisivo.")
    if h2h.get("recent"):
        linhas.append("Últimos confrontos:")
        for jogo in h2h["recent"][:3]:
            linhas.append(f"• {jogo['date'][:10]} — {jogo['home']} {jogo['score']} {jogo['away']}")
    linhas.extend([
        "",
        "Dados descritivos da temporada; não são previsão independente nem probabilidade calibrada.",
        "A análise não altera a probabilidade sem vig da Pinnacle nem o cálculo de EV.",
    ])
    return "\n".join(linhas)


async def listar_jogos(sport, date=None):
    sport = sport.upper()
    if sport not in ("NBA", "NFL"):
        raise SportsAPIError("Use NBA ou NFL.")
    if not date:
        date = datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)):
        raise SportsAPIError("Data inválida. Use AAAA-MM-DD.")

    if sport == "NBA":
        params = {
            "date": date, "league": NBA_LEAGUE, "season": NBA_SEASON,
            "timezone": TIMEZONE,
        }
    else:
        league = await _nfl_league()
        params = {
            "date": date, "league": league["id"], "season": NFL_SEASON,
            "timezone": TIMEZONE,
        }
    body = await _request(sport, "games", params, ttl=300)
    return date, _response_list(body)


def formatar_jogos(sport, date, games):
    linhas = [f"🏟️ {sport} — jogos de {date}"]
    if not games:
        linhas.append("A API não retornou partidas para esta data.")
        return "\n".join(linhas)
    for game in games[:20]:
        home, away = _game_teams(game)
        home_name = home.get("name") or "Mandante"
        away_name = away.get("name") or "Visitante"
        home_score, away_score = _game_scores(game)
        status = game.get("status") or {}
        if isinstance(status, dict):
            situacao = status.get("long") or status.get("short") or "Status indisponível"
            clock = status.get("clock")
        else:
            situacao, clock = str(status), None
        if home_score is not None and away_score is not None:
            placar = f"{int(home_score)}–{int(away_score)}"
        else:
            placar = "vs"
        hora = _game_date(game)
        if "T" in hora:
            hora = hora[11:16] + " UTC"
        linhas.append(
            f"• {hora} | {away_name} {placar} {home_name} | {situacao}"
            + (f" ({clock})" if clock else "")
        )
    if len(games) > 20:
        linhas.append(f"... e mais {len(games) - 20} partidas.")
    return "\n".join(linhas)
