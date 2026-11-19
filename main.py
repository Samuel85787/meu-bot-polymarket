"""
Bot de Monitoramento de Arbitragem Polymarket

Monitora mercados de previsão na Polymarket em tempo real,
detecta oportunidades de arbitragem e alertas sobre baleias.
"""

import asyncio
import sys
import logging
from pathlib import Path

# Adiciona o diretório raiz ao path
sys.path.insert(0, str(Path(__file__).parent))

from src.bot_telegram import main

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot encerrado pelo usuário.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Erro fatal: {type(e).__name__}: {e}")
        sys.exit(1)