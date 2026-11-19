import time
import logging
from collections import defaultdict, deque
from typing import Optional, Tuple, Dict

logger = logging.getLogger(__name__)

class PriceTracker:
    """
    Rastreia histórico de preços e detecta picos usando EMA (Exponential Moving Average).
    """
    
    def __init__(self, threshold: float, window: int):
        """
        Args:
            threshold: Limite percentual para considerar um pico (ex: 0.05 = 5%)
            window: Janela de tempo em segundos para manter histórico
        """
        self.threshold = threshold
        self.window = window
        self.history: Dict[str, deque] = defaultdict(deque)
        self.ema: Dict[str, float] = {}  # Armazena a Média Móvel Exponencial

    def add_price(self, token_id: str, price: float) -> Optional[Tuple[str, float, float, float]]:
        """
        Adiciona um novo preço ao histórico e detecta picos.
        
        Args:
            token_id: ID do token
            price: Preço atual
        
        Returns:
            Tuple com (event_type, old_price, new_price, pct_change) se houver pico
            None se não houver pico
        """
        try:
            now = time.time()
            dq = self.history[token_id]
            dq.append((now, price))

            # 1. ATUALIZA O EMA (Média Móvel Exponencial)
            alpha = 0.2  # Fator de suavização (quanto maior, mais peso no preço atual)
            if token_id not in self.ema:
                self.ema[token_id] = price
            else:
                self.ema[token_id] = (price * alpha) + (self.ema[token_id] * (1 - alpha))
            
            ema_val = self.ema[token_id]

            # 2. LIMPA HISTÓRICO VELHO
            while dq and now - dq[0][0] > self.window:
                dq.popleft()

            # 3. PRECISA DE ALGUNS DADOS PARA TER UMA BASE
            if len(dq) < 5:
                return None

            # 4. DETECÇÃO DE PICO (Baseado no EMA)
            pct_from_ema = (price - ema_val) / ema_val if ema_val > 0 else 0
            
            if abs(pct_from_ema) >= self.threshold:
                dq.clear()  # Zera a fila para não mandar spam do mesmo evento
                logger.info(f"📊 Pico detectado para {token_id}: {pct_from_ema*100:.2f}%")
                return ("SPIKE", ema_val, price, pct_from_ema)

            return None
        
        except Exception as e:
            logger.error(f"Erro ao adicionar preço: {type(e).__name__}: {e}")
            return None