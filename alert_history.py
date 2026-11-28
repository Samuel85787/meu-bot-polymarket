import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager

import aiohttp

import config

logger = logging.getLogger(__name__)
DB_PATH = os.getenv("ALERT_HISTORY_DB", "alert_history.db")


@contextmanager
def _connection():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize():
    with _connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                source TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                market_id TEXT,
                condition_id TEXT,
                token_id TEXT,
                outcome TEXT,
                entry_price REAL,
                reference_prob REAL,
                ev_pct REAL,
                stake_usd REAL,
                details TEXT,
                resolved_at REAL,
                won INTEGER,
                settlement_value REAL,
                pnl_usd REAL,
                roi_pct REAL,
                sport TEXT
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_unresolved ON alerts(resolved_at, market_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at)")
        # Migração aditiva: mantém bancos SQLite já existentes compatíveis.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(alerts)")}
        if "settlement_value" not in columns:
            db.execute("ALTER TABLE alerts ADD COLUMN settlement_value REAL")
        if "pnl_usd" not in columns:
            db.execute("ALTER TABLE alerts ADD COLUMN pnl_usd REAL")
        if "roi_pct" not in columns:
            db.execute("ALTER TABLE alerts ADD COLUMN roi_pct REAL")
        if "sport" not in columns:
            db.execute("ALTER TABLE alerts ADD COLUMN sport TEXT")


def record_alert(source, alert_type, market, token_id=None, outcome=None,
                 entry_price=None, reference_prob=None, ev_pct=None,
                 stake_usd=None, details=None):
    market = market or {}
    try:
        with _connection() as db:
            db.execute("""
                INSERT INTO alerts (
                    created_at, source, alert_type, market_id, condition_id,
                    token_id, outcome, entry_price, reference_prob, ev_pct,
                    stake_usd, details, sport
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(), str(source), str(alert_type),
                str(market.get("id") or "") or None,
                str(market.get("conditionId") or "") or None,
                str(token_id or "") or None, str(outcome or "") or None,
                _finite_or_none(entry_price), _finite_or_none(reference_prob),
                _finite_or_none(ev_pct), _finite_or_none(stake_usd),
                json.dumps(details or {}, ensure_ascii=False, default=str),
                str(market.get("_sport") or (market.get("sport") or "")) or None,
            ))
        return True
    except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
        logger.warning("Não foi possível registrar alerta: %s", type(exc).__name__)
        return False


def _finite_or_none(value):
    if value is None:
        return None
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    except (TypeError, ValueError):
        return None


def simulated_result(alert_type, entry_price, stake_usd, details, outcome):
    """Retorno hipotético com liquidação binária ou parcial (ex.: empate 50/50)."""
    if str(alert_type).strip().lower() not in {"ev líquido", "odds value"}:
        return None, None
    price = _finite_or_none(entry_price)
    stake = _finite_or_none(stake_usd)
    if price is None or stake is None or not 0 < price < 1 or stake <= 0:
        return None, None
    if isinstance(outcome, bool):
        payout_value = 1.0 if outcome else 0.0
    else:
        payout_value = _finite_or_none(outcome)
    if payout_value is None or not 0 <= payout_value <= 1:
        return None, None
    if not isinstance(details, dict):
        try:
            details = json.loads(details or "{}")
        except (TypeError, ValueError):
            details = {}
    gross_shares = _finite_or_none(details.get("gross_shares"))
    net_shares = _finite_or_none(details.get("net_shares"))
    if gross_shares is None:
        gross_shares = stake / price
    if net_shares is None:
        fee_usd = _finite_or_none(details.get("fee_usd")) or 0.0
        net_shares = max(0.0, gross_shares - fee_usd / price)
    pnl = net_shares * payout_value - stake
    return pnl, pnl / stake * 100

def get_metrics():
    with _connection() as db:
        total = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE token_id IS NOT NULL AND resolved_at IS NULL"
        ).fetchone()[0]
        settled = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE resolved_at IS NOT NULL"
        ).fetchone()[0]
        draws = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE resolved_at IS NOT NULL AND won IS NULL"
        ).fetchone()[0]
        wins = db.execute("SELECT COUNT(*) FROM alerts WHERE won = 1").fetchone()[0]
        losses = db.execute("SELECT COUNT(*) FROM alerts WHERE won = 0").fetchone()[0]
        odds_counts = db.execute("""
            SELECT SUM(CASE WHEN won IS NOT NULL THEN 1 ELSE 0 END) AS decisive,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND won IS NULL THEN 1 ELSE 0 END) AS draws,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses
            FROM alerts WHERE source = 'odds_value'
        """).fetchone()
        avg_ev = db.execute(
            "SELECT AVG(ev_pct) FROM alerts WHERE ev_pct IS NOT NULL"
        ).fetchone()[0]
        pnl = db.execute(
            "SELECT COUNT(*) AS samples, SUM(pnl_usd) AS pnl_usd, AVG(roi_pct) AS avg_roi_pct "
            "FROM alerts WHERE pnl_usd IS NOT NULL"
        ).fetchone()
        calibration = db.execute("""
            SELECT COUNT(*) AS samples,
                   AVG((reference_prob - won) * (reference_prob - won)) AS brier_score,
                   AVG(ABS(reference_prob - won)) AS mean_abs_error
            FROM alerts
            WHERE source = 'odds_value' AND won IS NOT NULL
              AND reference_prob > 0 AND reference_prob < 1
        """).fetchone()
        calibration_bins = db.execute("""
            SELECT CASE
                     WHEN reference_prob < 0.50 THEN '<50%'
                     WHEN reference_prob < 0.60 THEN '50–59%'
                     WHEN reference_prob < 0.70 THEN '60–69%'
                     WHEN reference_prob < 0.80 THEN '70–79%'
                     ELSE '80%+'
                   END AS probability_band,
                   COUNT(*) AS samples, AVG(reference_prob) AS predicted,
                   AVG(won) AS observed
            FROM alerts
            WHERE source = 'odds_value' AND won IS NOT NULL
              AND reference_prob > 0 AND reference_prob < 1
            GROUP BY probability_band ORDER BY MIN(reference_prob)
        """).fetchall()
        by_sport = db.execute("""
            SELECT COALESCE(sport, 'Não identificado') AS sport,
                   COUNT(*) AS total,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND won IS NULL THEN 1 ELSE 0 END) AS draws,
                   COUNT(pnl_usd) AS pnl_samples, SUM(pnl_usd) AS pnl_usd,
                   AVG(roi_pct) AS avg_roi_pct
            FROM alerts WHERE source = 'odds_value'
            GROUP BY sport ORDER BY total DESC
        """).fetchall()
        by_type = db.execute("""
            SELECT alert_type, COUNT(*) AS total,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND won IS NULL THEN 1 ELSE 0 END) AS draws,
                   COUNT(pnl_usd) AS pnl_samples, SUM(pnl_usd) AS pnl_usd,
                   AVG(roi_pct) AS avg_roi_pct
            FROM alerts GROUP BY alert_type ORDER BY total DESC
        """).fetchall()
    return {
        "total": total, "pending": pending, "settled": settled,
        "wins": wins, "losses": losses,
        "win_rate": (wins / (wins + losses) * 100) if (wins + losses) else None,
        "odds_settled": odds_counts["decisive"] or 0,
        "odds_draws": odds_counts["draws"] or 0,
        "odds_wins": odds_counts["wins"] or 0,
        "odds_losses": odds_counts["losses"] or 0,
        "odds_win_rate": ((odds_counts["wins"] or 0) / odds_counts["decisive"] * 100)
                         if odds_counts["decisive"] else None,
        "avg_ev": avg_ev,
        "pnl_samples": pnl["samples"],
        "simulated_pnl_usd": pnl["pnl_usd"],
        "avg_roi_pct": pnl["avg_roi_pct"],
        "calibration": dict(calibration),
        "calibration_bins": [dict(row) for row in calibration_bins],
        "by_sport": [dict(row) for row in by_sport],
        "by_type": [dict(row) for row in by_type],
    }


def get_sport_metrics(sport):
    with _connection() as db:
        row = db.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND won IS NULL THEN 1 ELSE 0 END) AS draws,
                   COUNT(pnl_usd) AS pnl_samples, SUM(pnl_usd) AS pnl_usd,
                   AVG(roi_pct) AS avg_roi_pct, AVG(ev_pct) AS avg_ev
            FROM alerts
            WHERE source = 'odds_value' AND LOWER(COALESCE(sport, '')) = LOWER(?)
        """, (str(sport).strip(),)).fetchone()
    return dict(row)


def replay_backtest(stake_usd=100.0, min_ev_pct=None, sport=None):
    """Reproduz alertas Odds Value encerrados, inclusive liquidações parciais."""
    stake = _finite_or_none(stake_usd)
    if stake is None or stake <= 0:
        raise ValueError("stake_usd deve ser positivo")
    clauses = [
        "source = 'odds_value'",
        "resolved_at IS NOT NULL",
        "(settlement_value IS NOT NULL OR won IS NOT NULL)",
        "entry_price IS NOT NULL",
    ]
    params = []
    if min_ev_pct is not None:
        threshold = _finite_or_none(min_ev_pct)
        if threshold is None:
            raise ValueError("min_ev_pct inválido")
        clauses.append("ev_pct >= ?")
        params.append(threshold)
    if sport:
        clauses.append("LOWER(COALESCE(sport, '')) = LOWER(?)")
        params.append(str(sport).strip())
    with _connection() as db:
        rows = db.execute(
            "SELECT alert_type, entry_price, stake_usd, details, won, settlement_value, ev_pct, sport "
            "FROM alerts WHERE " + " AND ".join(clauses) + " ORDER BY created_at ASC",
            params,
        ).fetchall()
    results = []
    for row in rows:
        historical_stake = _finite_or_none(row["stake_usd"])
        if not historical_stake or historical_stake <= 0:
            continue
        outcome_value = row["settlement_value"]
        if outcome_value is None:
            outcome_value = bool(row["won"])
        pnl, roi = simulated_result(
            row["alert_type"], row["entry_price"], historical_stake,
            row["details"], outcome_value,
        )
        if roi is None:
            continue
        results.append({
            "sport": row["sport"] or "Não identificado",
            "won": True if outcome_value is True or outcome_value == 1 else (False if outcome_value is False or outcome_value == 0 else None),
            "ev_pct": row["ev_pct"],
            "roi_pct": roi,
            "pnl_usd": stake * roi / 100.0,
        })
    cumulative = peak = max_drawdown = 0.0
    for item in results:
        cumulative += item["pnl_usd"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    wins = sum(1 for item in results if item["won"] is True)
    losses = sum(1 for item in results if item["won"] is False)
    draws = sum(1 for item in results if item["won"] is None)
    return {
        "samples": len(results), "wins": wins, "losses": losses, "draws": draws,
        "win_rate": wins / (wins + losses) * 100 if (wins + losses) else None,
        "total_pnl_usd": sum(item["pnl_usd"] for item in results) if results else None,
        "avg_roi_pct": sum(item["roi_pct"] for item in results) / len(results) if results else None,
        "max_drawdown_usd": max_drawdown if results else None,
        "by_sport": {
            name: {
                "samples": sum(1 for item in results if item["sport"] == name),
                "draws": sum(1 for item in results if item["sport"] == name and item["won"] is None),
                "pnl_usd": sum(item["pnl_usd"] for item in results if item["sport"] == name),
            }
            for name in sorted({item["sport"] for item in results})
        },
        "assumptions": "Replay de alertas Odds Value armazenados; inclui liquidações parciais conhecidas, mas não sinais não registrados nem execução real.",
    }

def get_extreme_alerts(limit=5, best=True):
    direction = "DESC" if best else "ASC"
    with _connection() as db:
        rows = db.execute(f"""
            SELECT created_at, sport, outcome, entry_price, reference_prob,
                   ev_pct, won, pnl_usd, roi_pct
            FROM alerts
            WHERE source = 'odds_value' AND ev_pct IS NOT NULL
            ORDER BY ev_pct {direction}, created_at DESC LIMIT ?
        """, (max(1, min(int(limit), 20)),)).fetchall()
    return [dict(row) for row in rows]



def get_period_metrics(hours=24):
    """Resumo dos alertas recentes; P&L é apenas hipotético para Odds Value."""
    periodo = _finite_or_none(hours)
    if periodo is None or periodo <= 0:
        raise ValueError("hours deve ser positivo")
    periodo = min(periodo, 24 * 365)
    cutoff = time.time() - periodo * 3600

    with _connection() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]
        by_source = db.execute(
            "SELECT source, COUNT(*) AS total FROM alerts "
            "WHERE created_at >= ? GROUP BY source", (cutoff,)
        ).fetchall()
        odds = db.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND won IS NULL THEN 1 ELSE 0 END) AS draws,
                   COUNT(pnl_usd) AS pnl_samples,
                   SUM(pnl_usd) AS simulated_pnl_usd,
                   AVG(roi_pct) AS avg_roi_pct,
                   AVG(ev_pct) AS avg_ev_pct
            FROM alerts
            WHERE source = 'odds_value' AND created_at >= ?
        """, (cutoff,)).fetchone()

    counts = {row["source"]: row["total"] for row in by_source}
    return {
        "hours": periodo,
        "total": total,
        "whale_alerts": counts.get("whale", 0),
        "spike_alerts": counts.get("price_spike", 0),
        "odds_alerts": odds["total"] or 0,
        "odds_pending": odds["pending"] or 0,
        "odds_wins": odds["wins"] or 0,
        "odds_losses": odds["losses"] or 0,
        "odds_draws": odds["draws"] or 0,
        "pnl_samples": odds["pnl_samples"] or 0,
        "simulated_pnl_usd": odds["simulated_pnl_usd"],
        "avg_roi_pct": odds["avg_roi_pct"],
        "avg_ev_pct": odds["avg_ev_pct"],
    }


def get_sport_ranking(days=30, min_samples=3):
    """Ranking por ROI simulado dos Odds Value encerrados; não inclui lucro real."""
    periodo = _finite_or_none(days)
    if periodo is None or periodo <= 0:
        raise ValueError("days deve ser positivo")
    periodo = min(periodo, 3650)
    minimo = max(1, min(int(min_samples), 100))
    cutoff = time.time() - periodo * 86400

    with _connection() as db:
        rows = db.execute("""
            SELECT COALESCE(sport, 'Não identificado') AS sport,
                   COUNT(pnl_usd) AS samples,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND won IS NULL THEN 1 ELSE 0 END) AS draws,
                   SUM(pnl_usd) AS simulated_pnl_usd,
                   AVG(roi_pct) AS avg_roi_pct,
                   AVG(ev_pct) AS avg_ev_pct
            FROM alerts
            WHERE source = 'odds_value'
              AND created_at >= ?
              AND pnl_usd IS NOT NULL
            GROUP BY sport
            HAVING COUNT(pnl_usd) >= ?
            ORDER BY avg_roi_pct DESC, samples DESC
        """, (cutoff, minimo)).fetchall()

    ranking = []
    for row in rows:
        item = dict(row)
        resolvidos = (item["wins"] or 0) + (item["losses"] or 0)
        item["draws"] = item["draws"] or 0
        item["win_rate"] = (
            (item["wins"] or 0) / resolvidos * 100 if resolvidos else None
        )
        ranking.append(item)
    return ranking


async def update_resolutions(limit=100):
    """Consulta mercados pendentes na Gamma e marca o resultado do token alertado."""
    with _connection() as db:
        rows = db.execute("""
            SELECT id, market_id, token_id, alert_type, entry_price, stake_usd, details
            FROM alerts
            WHERE token_id IS NOT NULL AND resolved_at IS NULL AND market_id IS NOT NULL
            ORDER BY created_at ASC LIMIT ?
        """, (int(limit),)).fetchall()
    if not rows:
        return 0

    timeout = aiohttp.ClientTimeout(total=20, connect=8)
    resolved = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for row in rows:
            try:
                url = f"{config.GAMMA_BASE_URL}/markets/{row['market_id']}"
                async with session.get(url) as response:
                    if response.status == 404:
                        continue
                    if response.status != 200:
                        continue
                    market = await response.json()
                if not isinstance(market, dict) or not market.get("closed"):
                    continue
                tokens = market.get("clobTokenIds") or []
                outcomes = market.get("outcomes") or []
                prices = market.get("outcomePrices") or []
                if isinstance(tokens, str):
                    tokens = json.loads(tokens)
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(prices, str):
                    prices = json.loads(prices)
                try:
                    index = [str(token) for token in tokens].index(str(row["token_id"]))
                    final_price = float(prices[index])
                except (ValueError, TypeError, IndexError, KeyError):
                    continue
                final_price = _finite_or_none(final_price)
                if final_price is None or not 0 <= final_price <= 1:
                    continue
                # Só aceita liquidação final binária ou o empate 50/50 conhecido;
                # outros preços intermediários podem ser estados não definitivos.
                if final_price >= 0.99:
                    settlement_value = 1.0
                    won = 1
                elif final_price <= 0.01:
                    settlement_value = 0.0
                    won = 0
                elif 0.49 <= final_price <= 0.51:
                    settlement_value = 0.5
                    won = None
                else:
                    continue
                pnl, roi = simulated_result(
                    row["alert_type"], row["entry_price"], row["stake_usd"],
                    row["details"], settlement_value,
                )
                with _connection() as db:
                    cursor = db.execute(
                        """UPDATE alerts
                           SET resolved_at = ?, won = ?, settlement_value = ?, pnl_usd = ?, roi_pct = ?
                           WHERE id = ? AND resolved_at IS NULL""",
                        (time.time(), won, settlement_value, pnl, roi, row["id"]),
                    )
                    resolved += cursor.rowcount
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, OSError) as exc:
                logger.warning("Falha ao atualizar resultado do alerta %s: %s", row["id"], type(exc).__name__)
    return resolved
