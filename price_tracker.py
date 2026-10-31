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

        if len(dq) < 5: # Precisa de pelo menos 5 cotações para ter uma base
            return None

        # 2. DETECÇÃO DE REVERSÃO (Stop Hunt / Armadilha)
        prices_in_window = [p for t, p in dq]
        max_price = max(prices_in_window)
        min_price = min(prices_in_window)

        # Reversão para baixo: Bateu um teto alto e agora está despencando
        if max_price > ema_val * (1 + self.threshold) and price < max_price * (1 - self.threshold/2):
            dq.clear() # Zera a fila para não mandar spam do mesmo evento
            return ("REVERSAL_DOWN", max_price, price, (price - max_price)/max_price)

        # Reversão para cima: Caiu muito e agora está subindo forte
        if min_price < ema_val * (1 - self.threshold) and price > min_price * (1 + self.threshold/2):
            dq.clear()
            return ("REVERSAL_UP", min_price, price, (price - min_price)/min_price)

        # 3. DETECÇÃO DE PICO NORMAL (Baseado no EMA, não no preço antigo bruto)
        pct_from_ema = (price - ema_val) / ema_val
        if abs(pct_from_ema) >= self.threshold:
            dq.clear()
            return ("SPIKE", ema_val, price, pct_from_ema)

        return None