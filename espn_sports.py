"""Dados esportivos atuais de NBA/NFL via feeds públicos da ESPN.

A ESPN não documenta oficialmente estes endpoints. Use cache conservador,
trate respostas incompletas como indisponíveis e nunca assuma uma quota publicada.
"""
import asyncio
import logging
import re
import time
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports"
TIMEZONE = "America/Recife"
SPORTS = {
    "NBA": {"path": "basketball/nba", "emoji": "🏀"},
    "NFL": {"path": "football/nfl", "emoji": "🏈"},
}
TEAM_CACHE_SECONDS = 24 * 60 * 60
SCOREBOARD_CACHE_SECONDS = 60
SCHEDULE_CACHE_SECONDS = 10 * 60
REQUEST_TIMEOUT = 15

_cache = {}
_locks = {}
_request_lock = asyncio.Lock()
_last_request_at = 0.0


class SportsDataError(Exception):
    """Erro recuperável ao consultar os feeds esportivos."""


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


async def _get(path, params=None, ttl=SCHEDULE_CACHE_SECONDS):
    global _last_request_at
    params = params or {}
    cache_key = (path, tuple(sorted((str(k), str(v)) for k, v in params.items())))
    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < ttl:
        return cached[1]

    lock = _locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < ttl:
            return cached[1]

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=6)
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        url = f"{BASE}/{path.lstrip('/')}"
        try:
            async with _request_lock:
                wait = 0.35 - (time.monotonic() - _last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                    async with session.get(url, params=params) as response:
                        _last_request_at = time.monotonic()
                        if response.status == 403:
                            raise SportsDataError(
                                "A ESPN recusou temporariamente o acesso ao feed público (HTTP 403)."
                            )
                        if response.status == 404:
                            raise SportsDataError("A ESPN não encontrou esse recurso.")
                        if response.status == 429:
                            raise SportsDataError(
                                "A ESPN aplicou uma limitação temporária. Tente novamente mais tarde."
                            )
                        if response.status >= 500:
                            raise SportsDataError(
                                f"O feed da ESPN está instável (HTTP {response.status})."
                            )
                        if response.status != 200:
                            raise SportsDataError(f"Feed ESPN respondeu HTTP {response.status}.")
                        try:
                            data = await response.json(content_type=None)
                        except (ValueError, aiohttp.ContentTypeError) as exc:
                            raise SportsDataError("A ESPN retornou uma resposta inválida.") from exc
        except SportsDataError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Falha no feed ESPN %s: %s", path, type(exc).__name__)
            raise SportsDataError("Não foi possível acessar os dados esportivos agora.") from exc

        if not isinstance(data, dict):
            raise SportsDataError("Formato inesperado recebido do feed ESPN.")
        _cache[cache_key] = (time.monotonic(), data)
        return data


def _walk_team_nodes(node):
    if isinstance(node, dict):
        team = node.get("team")
        if isinstance(team, dict) and (team.get("id") or team.get("abbreviation")):
            yield team
        elif (node.get("id") or node.get("abbreviation")) and (
            node.get("displayName") or node.get("name")
        ):
            yield node
        for value in node.values():
            yield from _walk_team_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_team_nodes(value)


async def _teams(sport):
    spec = SPORTS[sport]
    payload = await _get(f"{spec['path']}/teams", ttl=TEAM_CACHE_SECONDS)
    unique = {}
    for team in _walk_team_nodes(payload):
        key = str(team.get("id") or team.get("abbreviation"))
        unique[key] = team
    if not unique:
        raise SportsDataError(
            f"A ESPN não retornou a lista de equipes de {sport}. Tente novamente mais tarde."
        )
    return list(unique.values())


def _team_aliases(team):
    aliases = {
        _norm(team.get("displayName")),
        _norm(team.get("shortDisplayName")),
        _norm(team.get("name")),
        _norm(team.get("abbreviation")),
        _norm(team.get("location")),
    }
    location, name = _norm(team.get("location")), _norm(team.get("name"))
    if location and name:
        aliases.add(f"{location} {name}")
    aliases.discard("")
    return aliases


def _resolve_team(teams, query):
    target = _norm(query)
    if not target:
        return None
    exact = [team for team in teams if target in _team_aliases(team)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    # Apelido parcial só é aceito quando identifica uma única equipe.
    matches = [
        team for team in teams
        if any(target == alias.split()[-1] for alias in _team_aliases(team))
    ]
    return matches[0] if len(matches) == 1 else None


async def _scoreboard(sport, date=None):
    spec = SPORTS[sport]
    params = {}
    if date:
        digits = str(date).replace("-", "")
        if not re.fullmatch(r"\d{8}", digits):
            raise SportsDataError("Data inválida. Use AAAA-MM-DD.")
        params["dates"] = digits
    return await _get(
        f"{spec['path']}/scoreboard",
        params=params,
        ttl=SCOREBOARD_CACHE_SECONDS,
    )


def _current_season(payload):
    leagues = payload.get("leagues") or []
    if leagues and isinstance(leagues[0], dict):
        season = leagues[0].get("season") or {}
        if season.get("year") is not None:
            return int(season["year"]), season.get("displayName") or str(season["year"])
    season = payload.get("season") or {}
    if season.get("year") is not None:
        return int(season["year"]), str(season["year"])
    raise SportsDataError("A ESPN não informou a temporada atual; análise bloqueada para evitar dados antigos.")


def _events(schedule):
    events = schedule.get("events")
    return events if isinstance(events, list) else []


async def _team_schedule(sport, team, season_year):
    spec = SPORTS[sport]
    abbreviation = str(team.get("abbreviation") or "").lower()
    if not abbreviation:
        raise SportsDataError("A ESPN não informou o código da equipe.")
    return await _get(
        f"{spec['path']}/teams/{abbreviation}/schedule",
        params={"season": season_year},
        ttl=SCHEDULE_CACHE_SECONDS,
    )


def _competition(event):
    competitions = event.get("competitions") or []
    return competitions[0] if competitions and isinstance(competitions[0], dict) else {}


def _competitors(event):
    return _competition(event).get("competitors") or []


def _team_id(competitor):
    team = competitor.get("team") or {}
    return str(team.get("id") or competitor.get("id") or "")


def _score(competitor):
    value = competitor.get("score")
    if isinstance(value, dict):
        value = value.get("value")
        if value is None:
            value = competitor.get("score", {}).get("displayValue")
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _game_finished(event):
    status = _competition(event).get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    return bool(status_type.get("completed")) or status_type.get("state") == "post"


def _event_date(event):
    return str(event.get("date") or _competition(event).get("date") or "")


def _result_for_team(event, team_id):
    competitors = _competitors(event)
    if len(competitors) < 2:
        return None
    own = next((x for x in competitors if _team_id(x) == str(team_id)), None)
    opp = next((x for x in competitors if _team_id(x) != str(team_id)), None)
    if own is None or opp is None or not _game_finished(event):
        return None

    own_score, opp_score = _score(own), _score(opp)
    if own_score is None or opp_score is None:
        return None

    winner = own.get("winner")
    if winner is True:
        outcome = "W"
    elif winner is False:
        outcome = "L"
    elif own_score > opp_score:
        outcome = "W"
    elif own_score < opp_score:
        outcome = "L"
    else:
        outcome = "T"

    opp_team = opp.get("team") or {}
    own_team = own.get("team") or {}
    return {
        "event_id": str(event.get("id") or _competition(event).get("id") or ""),
        "date": _event_date(event),
        "outcome": outcome,
        "points_for": own_score,
        "points_against": opp_score,
        "opponent": opp_team.get("displayName") or opp_team.get("name") or "Adversário",
        "home_away": own.get("homeAway") or "",
        "own_name": own_team.get("displayName") or own_team.get("name") or "",
        "opponent_name": opp_team.get("displayName") or opp_team.get("name") or "",
    }


def _record(events, team_id):
    games = []
    for event in events:
        result = _result_for_team(event, team_id)
        if result:
            games.append(result)
    unique = {x["event_id"]: x for x in games if x["event_id"]}
    games = list(unique.values())
    games.sort(key=lambda x: x["date"], reverse=True)

    wins = sum(x["outcome"] == "W" for x in games)
    losses = sum(x["outcome"] == "L" for x in games)
    ties = sum(x["outcome"] == "T" for x in games)
    last_five = games[:5]
    count = len(games)
    return {
        "games": count,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_pct": wins / count if count else None,
        "last_five": last_five,
        "last_five_wins": sum(x["outcome"] == "W" for x in last_five),
        "last_five_losses": sum(x["outcome"] == "L" for x in last_five),
        "avg_for": sum(x["points_for"] for x in games) / count if count else None,
        "avg_against": sum(x["points_against"] for x in games) / count if count else None,
    }


def _h2h(events_a, events_b, id_a, id_b):
    a_events = {
        str(event.get("id") or _competition(event).get("id")): event
        for event in events_a
        if event.get("id") or _competition(event).get("id")
    }
    b_events = {
        str(event.get("id") or _competition(event).get("id")): event
        for event in events_b
        if event.get("id") or _competition(event).get("id")
    }
    common = set(a_events).intersection(b_events)
    games = []
    for event_id in common:
        event = a_events[event_id]
        if not _game_finished(event):
            continue
        result = _result_for_team(event, id_a)
        if result:
            result["winner_id"] = id_a if result["outcome"] == "W" else (
                id_b if result["outcome"] == "L" else None
            )
            games.append(result)
    games.sort(key=lambda x: x["date"], reverse=True)
    return {
        "total": len(games),
        "wins_home": sum(x["winner_id"] == id_a for x in games),
        "wins_away": sum(x["winner_id"] == id_b for x in games),
        "recent": games[:3],
    }


async def analisar_confronto(sport, home_name, away_name):
    sport = str(sport).upper()
    if sport not in SPORTS:
        raise SportsDataError("Escolha NBA ou NFL.")

    teams, scoreboard = await asyncio.gather(
        _teams(sport), _scoreboard(sport)
    )
    season_year, season_display = _current_season(scoreboard)
    home_team = _resolve_team(teams, home_name)
    away_team = _resolve_team(teams, away_name)
    if not home_team or not away_team:
        return {
            "status": "not_found",
            "sport": sport,
            "season": season_display,
            "teams": [
                team.get("displayName") or team.get("name")
                for team in teams if team.get("displayName") or team.get("name")
            ],
        }

    id_home = str(home_team.get("id") or "")
    id_away = str(away_team.get("id") or "")
    if not id_home or not id_away:
        raise SportsDataError("A ESPN não retornou identificadores válidos para as equipes.")
    if id_home == id_away:
        return {"status": "same_team", "sport": sport}

    home_schedule, away_schedule = await asyncio.gather(
        _team_schedule(sport, home_team, season_year),
        _team_schedule(sport, away_team, season_year),
    )
    events_home, events_away = _events(home_schedule), _events(away_schedule)

    # Não usa temporada antiga em caso de ausência da temporada corrente.
    home_record = _record(events_home, id_home)
    away_record = _record(events_away, id_away)
    if not home_record["games"] and not away_record["games"]:
        raise SportsDataError(
            f"A ESPN ainda não retornou jogos concluídos da temporada {season_display} "
            f"para {home_team.get('displayName')} e {away_team.get('displayName')}."
        )

    return {
        "status": "ok",
        "sport": sport,
        "season": season_display,
        "season_year": season_year,
        "source": "ESPN",
        "updated_at": datetime.now(ZoneInfo(TIMEZONE)).strftime("%H:%M"),
        "home": {
            "id": id_home,
            "name": home_team.get("displayName") or home_team.get("name") or home_name,
            "abbreviation": home_team.get("abbreviation"),
            "record": home_record,
        },
        "away": {
            "id": id_away,
            "name": away_team.get("displayName") or away_team.get("name") or away_name,
            "abbreviation": away_team.get("abbreviation"),
            "record": away_record,
        },
        "h2h": _h2h(events_home, events_away, id_home, id_away),
    }


def _form_text(record):
    games = record.get("last_five") or []
    if not games:
        return "sem jogos concluídos na temporada"
    sequence = " ".join(x["outcome"] for x in games)
    return (
        f"{record['wins']}V–{record['losses']}D"
        + (f"–{record['ties']}E" if record.get("ties") else "")
        + f" na temporada  •  últimos {len(games)}: {sequence}"
    )


def formatar_analise(analysis):
    if analysis.get("status") == "same_team":
        return "⚠️ Você informou a mesma equipe duas vezes. Escolha dois times diferentes."
    if analysis.get("status") != "ok":
        candidates = analysis.get("teams") or []
        extra = "\n\nTimes disponíveis: " + " · ".join(candidates[:8]) if candidates else ""
        return "🔎 Não identifiquei as duas equipes com segurança." + extra

    home, away = analysis["home"], analysis["away"]
    emoji = SPORTS[analysis["sport"]]["emoji"]
    h2h = analysis["h2h"]
    lines = [
        f"{emoji} ANÁLISE DE CONFRONTO  •  {analysis['sport']}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏟️ {home['name']}  x  {away['name']}",
        f"🗓️ Temporada atual: {analysis['season']}",
        f"📡 Fonte: ESPN  •  atualizado às {analysis['updated_at']} (Recife)",
        "",
        "📈 FORMA E CAMPANHA",
        f"🏠 {home['name']}",
        f"   {_form_text(home['record'])}",
        f"✈️ {away['name']}",
        f"   {_form_text(away['record'])}",
    ]
    for label, team in (("Mandante", home), ("Visitante", away)):
        record = team["record"]
        if record.get("avg_for") is not None and record.get("avg_against") is not None:
            lines.append(
                f"   {label}: {record['avg_for']:.1f} marcados  /  "
                f"{record['avg_against']:.1f} sofridos por jogo"
            )

    lines.extend([
        "",
        "⚔️ CONFRONTO DIRETO NA TEMPORADA",
        f"{home['name']}  {h2h['wins_home']} x {h2h['wins_away']}  {away['name']}",
        f"Amostra: {h2h['total']} jogo(s)",
    ])
    if h2h["total"] < 2:
        lines.append("ⓘ Amostra pequena; H2H não deve ter peso decisivo.")
    for game in h2h.get("recent", [])[:3]:
        data = game.get("date", "")[:10]
        lines.append(
            f"• {data}: {game['own_name']} {int(game['points_for'])}–"
            f"{int(game['points_against'])} {game['opponent_name']}"
        )

    lines.extend([
        "",
        "ℹ️ Leitura descritiva da temporada vigente.",
        "Não é uma previsão nem uma probabilidade calibrada; não altera o EV da Pinnacle.",
    ])
    return "\n".join(lines)


async def listar_jogos(sport, date=None):
    sport = str(sport).upper()
    if sport not in SPORTS:
        raise SportsDataError("Escolha NBA ou NFL.")
    scoreboard = await _scoreboard(sport, date)
    year, season_display = _current_season(scoreboard)
    events = scoreboard.get("events") or []
    return {
        "sport": sport,
        "season": season_display,
        "year": year,
        "date": date,
        "events": events,
        "updated_at": datetime.now(ZoneInfo(TIMEZONE)).strftime("%H:%M"),
    }


def _local_time(value):
    if not value:
        return "horário a confirmar"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.astimezone(ZoneInfo(TIMEZONE)).strftime("%H:%M")
    except (TypeError, ValueError):
        return "horário a confirmar"


def formatar_jogos(data):
    sport = data["sport"]
    emoji = SPORTS[sport]["emoji"]
    season = data["season"]
    date_label = data.get("date") or "agenda atual"
    events = data.get("events") or []
    lines = [
        f"{emoji} JOGOS {sport}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🗓️ {date_label}  •  temporada {season}",
        "",
    ]
    if not events:
        lines.append("📭 Nenhum jogo encontrado para essa data.")
    else:
        for event in events[:15]:
            competition = _competition(event)
            competitors = event.get("competitions", [{}])[0].get("competitors", [])
            home = next((x for x in competitors if x.get("homeAway") == "home"), None)
            away = next((x for x in competitors if x.get("homeAway") == "away"), None)
            if not home or not away:
                continue

            status = competition.get("status") or {}
            status_type = status.get("type") or {}
            desc = status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or "Agendado"
            home_name = (home.get("team") or {}).get("shortDisplayName") or (home.get("team") or {}).get("displayName") or "Mandante"
            away_name = (away.get("team") or {}).get("shortDisplayName") or (away.get("team") or {}).get("displayName") or "Visitante"
            home_score = home.get("score")
            away_score = away.get("score")
            if home_score is not None and away_score is not None:
                placar = f"{away_score}  x  {home_score}"
            else:
                placar = "vs"
            lines.extend([
                f"🕒 {_local_time(event.get('date') or competition.get('date'))} (Recife)",
                f"{away_name}  {placar}  {home_name}",
                f"   {desc}",
                "",
            ])
    lines.append(f"📡 ESPN • atualização às {data['updated_at']} (Recife)")
    return "\n".join(lines)


def formatar_status_fontes():
    return "\n".join([
        "🌐 FONTES DE DADOS ESPORTIVOS",
        "━━━━━━━━━━━━━━━━━━━━",
        "🏀 NBA  •  ESPN • temporada vigente",
        "🏈 NFL  •  ESPN • temporada vigente",
        "",
        "✅ Acesso público, sem chave ou assinatura.",
        "🧠 Cache local para reduzir chamadas repetidas.",
        "⚠️ A ESPN não publica limites oficiais nem garante estabilidade desses endpoints.",
        "Se a fonte falhar, o bot informa indisponibilidade e não substitui os dados por temporadas antigas.",
    ])
