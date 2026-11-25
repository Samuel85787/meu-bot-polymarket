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
                won INTEGER
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_unresolved ON alerts(resolved_at, market_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at)")


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
                    stake_usd, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(), str(source), str(alert_type),
                str(market.get("id") or "") or None,
                str(market.get("conditionId") or "") or None,
                str(token_id or "") or None, str(outcome or "") or None,
                _finite_or_none(entry_price), _finite_or_none(reference_prob),
                _finite_or_none(ev_pct), _finite_or_none(stake_usd),
                json.dumps(details or {}, ensure_ascii=False, default=str),
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


def get_metrics():
    with _connection() as db:
        total = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE token_id IS NOT NULL AND resolved_at IS NULL"
        ).fetchone()[0]
        settled = db.execute(
            "SELECT COUNT(*) FROM alerts WHERE won IS NOT NULL"
        ).fetchone()[0]
        wins = db.execute("SELECT COUNT(*) FROM alerts WHERE won = 1").fetchone()[0]
        losses = db.execute("SELECT COUNT(*) FROM alerts WHERE won = 0").fetchone()[0]
        avg_ev = db.execute(
            "SELECT AVG(ev_pct) FROM alerts WHERE ev_pct IS NOT NULL"
        ).fetchone()[0]
        by_type = db.execute("""
            SELECT alert_type, COUNT(*) AS total,
                   SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN won = 0 THEN 1 ELSE 0 END) AS losses
            FROM alerts GROUP BY alert_type ORDER BY total DESC
        """).fetchall()
    return {
        "total": total, "pending": pending, "settled": settled,
        "wins": wins, "losses": losses,
        "win_rate": (wins / settled * 100) if settled else None,
        "avg_ev": avg_ev,
        "by_type": [dict(row) for row in by_type],
    }


async def update_resolutions(limit=100):
    """Consulta mercados pendentes na Gamma e marca o resultado do token alertado."""
    with _connection() as db:
        rows = db.execute("""
            SELECT id, market_id, token_id FROM alerts
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
                if final_price >= 0.99:
                    won = 1
                elif final_price <= 0.01:
                    won = 0
                else:
                    continue
                with _connection() as db:
                    cursor = db.execute(
                        "UPDATE alerts SET resolved_at = ?, won = ? WHERE id = ? AND resolved_at IS NULL",
                        (time.time(), won, row["id"]),
                    )
                    resolved += cursor.rowcount
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, OSError) as exc:
                logger.warning("Falha ao atualizar resultado do alerta %s: %s", row["id"], type(exc).__name__)
    return resolved
