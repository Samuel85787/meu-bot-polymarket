import time

_services = {}


def set_service(name, state, detail=None):
    _services[str(name)] = {
        "state": str(state),
        "detail": str(detail) if detail else "",
        "updated_at": time.monotonic(),
    }


def get_services():
    now = time.monotonic()
    result = {}
    for name, item in _services.items():
        age = max(0, int(now - item["updated_at"]))
        result[name] = {
            "state": item["state"],
            "detail": item["detail"],
            "age_seconds": age,
        }
    return result


def format_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}min"


def format_status():
    services = get_services()
    if not services:
        return "Diagnóstico ainda inicializando."
    labels = {
        "telegram_listener": "Telegram (comandos)",
        "telegram_sender": "Telegram (envio)",
        "clob_ws": "Polymarket CLOB",
        "sports_ws": "Feed de placares",
        "gamma_api": "Gamma API (mercados)",
        "odds_api": "Odds API",
        "alert_history": "Histórico",
    }
    lines = []
    for key, label in labels.items():
        item = services.get(key)
        if not item:
            lines.append(f"{label}: ⚪ Sem dados")
            continue
        state = item["state"]
        icon = "🟢" if state == "ok" else "🔴" if state == "error" else "🟡"
        detail = f" — {item['detail']}" if item["detail"] else ""
        age = format_age(item["age_seconds"])
        lines.append(f"{icon} {label}: {state.upper()} ({age}){detail}")
    return "\n".join(lines)
