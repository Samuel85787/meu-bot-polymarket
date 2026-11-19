import os
from dotenv import load_dotenv

# Carrega variáveis do arquivo .env
load_dotenv()

# ============================================
# CREDENCIAIS (Do arquivo .env)
# ============================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# ============================================
# CONFIGURAÇÕES DE PREÇO E MONITORAMENTO
# ============================================
PRICE_SPIKE_THRESHOLD = float(os.getenv("PRICE_SPIKE_THRESHOLD", "0.05"))
PRICE_SPIKE_WINDOW = int(os.getenv("PRICE_SPIKE_WINDOW", "600"))
MAX_MARKETS_TO_MONITOR = int(os.getenv("MAX_MARKETS_TO_MONITOR", "500"))

# ============================================
# URLS E LIMITES DE PREÇO
# ============================================
POLYMARKET_BASE_URL = "https://polymarket.com"
MAX_PRICE = float(os.getenv("MAX_PRICE", "0.50"))
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.05"))

# ============================================
# CONFIGURAÇÕES DE APOSTAS E BALEIAS
# ============================================
MIN_BET_AMOUNT = float(os.getenv("MIN_BET_AMOUNT", "1000.0"))
MEGA_BET_AMOUNT = float(os.getenv("MEGA_BET_AMOUNT", "25000.0"))
ENABLE_WHALE_ALERTS = os.getenv("ENABLE_WHALE_ALERTS", "True").lower() == "true"
WHALE_LIQUIDITY_RATIO = float(os.getenv("WHALE_LIQUIDITY_RATIO", "0.10"))

# ============================================
# VALIDAÇÃO DE CONFIGURAÇÕES CRÍTICAS
# ============================================
def validar_config():
    """Valida se as configurações críticas estão preenchidas"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("❌ TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID são obrigatórios no .env")
    if not ODDS_API_KEY:
        print("⚠️ AVISO: ODDS_API_KEY não configurada. Verificação de Odds Value desativada.")

# Executa validação ao importar
validar_config()