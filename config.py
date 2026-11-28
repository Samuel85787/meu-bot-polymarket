import math
import os

from dotenv import load_dotenv

load_dotenv()


def numero(nome, padrao, minimo=0):
    valor = float(os.getenv(nome, str(padrao)))
    if not math.isfinite(valor) or valor < minimo:
        raise ValueError(f"Configuração inválida: {nome}")
    return valor


def inteiro(nome, padrao, minimo=1):
    valor = int(os.getenv(nome, str(padrao)))
    if valor < minimo:
        raise ValueError(f"Configuração inválida: {nome}")
    return valor


def booleano(nome, padrao=False):
    raw = os.getenv(nome, "true" if padrao else "false")
    valor = str(raw).strip().lower()
    if valor not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"Configuração inválida: {nome}")
    return valor in {"true", "1", "yes"}


TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "9010673985:AAHCbpzfSuSt9QT_tAj5_BOWX6jx9II9qIo",
).strip()
TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "1829037660",
).strip()

# Opcional: útil para restringir comandos dentro de um grupo.
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID", "").strip()

ODDS_API_KEY = os.getenv(
    "ODDS_API_KEY",
    "b197e7f30e189f120132c839abfd1409",
).strip()

POLYMARKET_BASE_URL = "https://polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"

PRICE_SPIKE_THRESHOLD = numero("PRICE_SPIKE_THRESHOLD", 0.05, 0.0001)
PRICE_SPIKE_WINDOW = inteiro("PRICE_SPIKE_WINDOW", 600)
MIN_SPIKE_SAMPLES = inteiro("MIN_SPIKE_SAMPLES", 3, 1)
MAX_MARKETS_TO_MONITOR = inteiro("MAX_MARKETS_TO_MONITOR", 1000)

MIN_PRICE = numero("MIN_PRICE", 0.03, 0.0001)
MAX_PRICE = numero("MAX_PRICE", 0.92, 0.0001)
FILTER_PRICE_RANGE = booleano("FILTER_PRICE_RANGE", True)

MIN_BET_AMOUNT = numero("MIN_BET_AMOUNT", 1000, 0.01)
MEGA_BET_AMOUNT = numero("MEGA_BET_AMOUNT", 25000, 0.01)
MIN_IMPACT_BET_AMOUNT = numero("MIN_IMPACT_BET_AMOUNT", 150, 0.01)
ENABLE_WHALE_ALERTS = booleano("ENABLE_WHALE_ALERTS", True)
WHALE_LIQUIDITY_RATIO = numero("WHALE_LIQUIDITY_RATIO", 0.12, 0.0001)

WHALE_COOLDOWN_SECONDS = inteiro("WHALE_COOLDOWN_SECONDS", 900)
SPIKE_COOLDOWN_SECONDS = inteiro("SPIKE_COOLDOWN_SECONDS", 180)
ODDS_COOLDOWN_SECONDS = inteiro("ODDS_COOLDOWN_SECONDS", 1800)

MARKET_REFRESH_SECONDS = inteiro("MARKET_REFRESH_SECONDS", 3600)
ODDS_CHECK_INTERVAL = inteiro("ODDS_CHECK_INTERVAL", 43200)
ODDS_EDGE_THRESHOLD = numero("ODDS_EDGE_THRESHOLD", 0.05, 0.0001)
ODDS_ESTIMATED_STAKE_USD = numero("ODDS_ESTIMATED_STAKE_USD", 100, 0.01)
ODDS_MAX_SPREAD_PCT = numero("ODDS_MAX_SPREAD_PCT", 8, 0)
ODDS_MAX_SLIPPAGE_PCT = numero("ODDS_MAX_SLIPPAGE_PCT", 3, 0)
ODDS_MIN_TOP_ASK_USD = numero("ODDS_MIN_TOP_ASK_USD", 25, 0)
ODDS_MAX_BOOK_AGE_SECONDS = inteiro("ODDS_MAX_BOOK_AGE_SECONDS", 30, 1)
ODDS_API_CREDITS_RESERVE = numero("ODDS_API_CREDITS_RESERVE", 50, 0)

# False conserva o comportamento original: aguardar /start.
AUTO_START = booleano("AUTO_START", False)

SETTINGS_FILE = os.getenv("SETTINGS_FILE", "settings.json")

# Mantidos como valores iniciais do projeto.
# Podem ser substituídos no Render, sem editar código.
SPORT_TAGS = {
    "NBA": os.getenv("TAG_NBA", "745"),
    "NFL": os.getenv("TAG_NFL", "450"),
    "Tênis ATP": os.getenv("TAG_ATP", "101232"),
    "Tênis WTA": os.getenv("TAG_WTA", "102123"),
    "CS2": os.getenv("TAG_CS2", "100780"),
    "VALORANT": os.getenv("TAG_VALORANT", "101672"),
}


def validar_config():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Configure TELEGRAM_BOT_TOKEN no Render.")

    if not TELEGRAM_CHAT_ID:
        raise ValueError("Configure TELEGRAM_CHAT_ID no Render.")

    int(TELEGRAM_CHAT_ID)

    if TELEGRAM_USER_ID:
        int(TELEGRAM_USER_ID)

    if not 0 < MIN_PRICE <= MAX_PRICE < 1:
        raise ValueError("Os preços devem satisfazer 0 < MIN_PRICE <= MAX_PRICE < 1.")
