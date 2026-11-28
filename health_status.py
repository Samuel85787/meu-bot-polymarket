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
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _service_line(services, key, label, missing_text=None):
    item = services.get(key)
    if not item:
        return f"🟡 {label}: {missing_text or 'aguardando primeira resposta'}"

    state = item["state"]
    detail = (item.get("detail") or "").strip()
    age = format_age(item.get("age_seconds", 0))

    if state == "ok":
        suffix = f" · {detail}" if detail else ""
        return f"🟢 {label}: OK{suffix} · {age}"
    if state == "error":
        suffix = f" · {detail}" if detail else ""
        return f"🔴 {label}: falha{suffix}"
    if state == "warning":
        suffix = f" · {detail}" if detail else ""
        return f"🟡 {label}: atenção{suffix}"
    if state == "checking":
        suffix = f" · {detail}" if detail else ""
        return f"🔄 {label}: verificando{suffix}"
    return f"⚪ {label}: {state}"


def format_status(monitor_active=True):
    """Compact, user-oriented health panel for the Telegram chat."""
    services = get_services()
    lines = []

    # Telegram is independent from the market monitor.
    listener = services.get("telegram_listener")
    sender = services.get("telegram_sender")
    if listener and listener.get("state") == "ok" and sender and sender.get("state") == "ok":
        lines.append("🟢 Telegram: comandos e envios OK")
    elif listener and listener.get("state") == "error":
        lines.append(f"🔴 Telegram: falha nos comandos · {listener.get('detail') or 'ver logs'}")
    elif sender and sender.get("state") == "error":
        lines.append(f"🔴 Telegram: falha no envio · {sender.get('detail') or 'ver logs'}")
    else:
        lines.append(_service_line(services, "telegram_listener", "Telegram", "inicializando"))

    lines.append(_service_line(services, "sports_ws", "Feed de placares", "aguardando conexão"))

    if monitor_active:
        lines.append(_service_line(services, "clob_ws", "Polymarket CLOB", "conectando"))
        lines.append(_service_line(services, "gamma_api", "Mercados Gamma", "aguardando consulta"))
    else:
        lines.append("⏸️ Polymarket CLOB e Gamma: pausados com o monitor")

    odds = services.get("odds_api")
    if odds:
        lines.append(_service_line(services, "odds_api", "Odds API"))
    else:
        lines.append("🟡 Odds API: aguardando primeira consulta")

    lines.append(_service_line(services, "alert_history", "Histórico", "aguardando ciclo"))
    return "\n".join(lines)
