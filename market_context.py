import re
import unicodedata


def _normalizar(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def contexto_mercado(market):
    market = market or {}
    events = market.get("events") or []
    event = events[0] if events and isinstance(events[0], dict) else {}
    sport = market.get("_sport") or market.get("sport") or event.get("sport") or "Não identificado"
    market_type = market.get("sportsMarketType") or market.get("marketType") or "Não identificado"
    candidates = [
        market.get("groupItemTitle"), market.get("period"), market.get("round"),
        market.get("map"), market.get("set"), market.get("description"),
        event.get("title"), event.get("name"),
    ]
    occasion = next((str(v).strip() for v in candidates if v and str(v).strip()), None)
    if not occasion and str(market_type).lower() == "moneyline":
        occasion = "Vencedor do evento (Moneyline)"
    if not occasion:
        occasion = "Ocasião não informada nos dados do mercado"
    return {
        "sport": str(sport),
        "market_type": str(market_type),
        "occasion": occasion,
        "question": market.get("question") or event.get("title") or "Evento não identificado",
    }


def formatar_contexto_mercado(market):
    ctx = contexto_mercado(market)
    return (
        f"Esporte: {ctx['sport']}\n"
        f"Ocasião: {ctx['occasion']}\n"
        f"Tipo de mercado: {ctx['market_type']}"
    )
