import time
from collections import defaultdict, deque

class PriceTracker:
    def __init__(self, threshold: float, window: int):
        self.threshold = threshold
        self.window = window
        self.history = defaultdict(deque)
        self.ema = {} # Armazena a Média Móvel de cada token

    def add_price(self, token_id: str, price: float):
        now = time.time()
        dq = self.history[token_id]
        dq.append((now, price))

        # 1. ATUALIZA O EMA (Média Móvel Exponencial)
        alpha = 0.2 # Fator de suavização
        if token_id not in self.ema:
            self.ema[token_id] = price
        else:
            self.ema[token_id] = (price * alpha) + (self.ema[token_id] * (1 - alpha))
        
        ema_val = self.ema[token_id]

        # Limpa histórico velho
        while dq and now - dq[0][0] > self.window:
            dq.popleft()

        if len(dq) < 5: # Precisa de algumas cotações para ter uma base
            return None

        # 2. DETECÇÃO DE PICO NORMAL (Baseado no EMA)
        pct_from_ema = (price - ema_val) / ema_val
        if abs(pct_from_ema) >= self.threshold:
            dq.clear() # Zera a fila para não mandar spam do mesmo evento
            return ("SPIKE", ema_val, price, pct_from_ema)

        return None