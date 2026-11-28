"""Classificações descritivas de sinais; não são previsões nem recomendações de aposta."""


def _numero(valor, padrao=0.0):
    try:
        numero = float(valor)
        if numero != numero or numero in (float("inf"), float("-inf")):
            return padrao
        return numero if numero >= 0 else padrao
    except (TypeError, ValueError):
        return padrao


def classificar_fluxo(buy_flow, sell_flow, buy_count, sell_count,
                      min_flow_usd, single_trade_usd=0.0,
                      impact_pct=0.0, min_impact_pct=12.0):
    """Distingue uma aposta isolada de fluxo repetido e de fluxo sem direção clara."""
    compra = _numero(buy_flow)
    venda = _numero(sell_flow)
    compras = max(0, int(buy_count or 0))
    vendas = max(0, int(sell_count or 0))
    minimo = max(0.01, _numero(min_flow_usd, 0.01))
    saldo = compra - venda
    total = compra + venda
    lado_compra = compra >= venda
    dominante = compra if lado_compra else venda
    quantidade_dominante = compras if lado_compra else vendas
    dominancia = dominante / total if total > 0 else 0.0

    recorrente = (
        quantidade_dominante >= 3
        and dominante >= minimo * 2
        and abs(saldo) >= minimo
        and dominancia >= 0.60
    )
    if recorrente:
        direcao = "COMPRA" if saldo > 0 else "VENDA"
        rotulo = "Fluxo comprador recorrente" if saldo > 0 else "Fluxo vendedor recorrente"
    elif total >= minimo * 2 and abs(saldo) < minimo:
        direcao = "MISTO"
        rotulo = "Movimentação dividida"
    elif _numero(single_trade_usd) >= minimo:
        direcao = "ISOLADA"
        rotulo = "Aposta individual de alto valor"
    elif _numero(impact_pct) >= _numero(min_impact_pct):
        direcao = "IMPACTO"
        rotulo = "Movimentação relevante frente à liquidez"
    else:
        direcao = "INDEFINIDA"
        rotulo = "Movimentação relevante"

    return {
        "label": rotulo,
        "direction": direcao,
        "buy_flow_usd": compra,
        "sell_flow_usd": venda,
        "net_flow_usd": saldo,
        "total_flow_usd": total,
        "dominance_pct": dominancia * 100,
        "recurrent": recorrente,
    }


def classificar_qualidade_odds(ev_data, minimum_edge_pct, max_spread_pct,
                               max_slippage_pct, stake_usd,
                               max_book_age_seconds):
    """Classifica condições de preço/execução, não a chance real de o resultado vencer."""
    if not isinstance(ev_data, dict):
        return {"label": "Dados insuficientes", "score": 0, "max_score": 7}

    ev = _numero(ev_data.get("net_ev_pct"), -1)
    spread = _numero(ev_data.get("spread_pct"), -1)
    slippage = _numero(ev_data.get("slippage_pct"), -1)
    depth = _numero(ev_data.get("top_ask_depth_usd"), -1)
    age = _numero(ev_data.get("book_age_seconds"), -1)
    minimum_edge = max(0.01, _numero(minimum_edge_pct, 0.01))
    max_spread = _numero(max_spread_pct)
    max_slippage = _numero(max_slippage_pct)
    stake = max(0.01, _numero(stake_usd, 0.01))
    max_age = max(1.0, _numero(max_book_age_seconds, 1.0))

    if min(ev, spread, slippage, depth, age) < 0:
        return {"label": "Dados insuficientes", "score": 0, "max_score": 7}

    score = 0
    if ev >= minimum_edge * 2:
        score += 2
    elif ev >= minimum_edge * 1.5:
        score += 1

    if max_spread > 0 and spread <= max_spread * 0.5:
        score += 1
    if max_slippage > 0 and slippage <= max_slippage * 0.5:
        score += 1
    if depth >= stake:
        score += 1
    if depth >= stake * 2:
        score += 1
    if age <= max_age * 0.5:
        score += 1

    if score >= 6:
        label = "Muito boa"
    elif score >= 4:
        label = "Boa"
    else:
        label = "No limite dos filtros"

    return {"label": label, "score": score, "max_score": 7}
