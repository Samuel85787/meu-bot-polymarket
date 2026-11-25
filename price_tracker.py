import math
import time
from collections import defaultdict, deque


class PriceTracker:
    def __init__(self, threshold: float, window: int, min_samples: int = 3, alpha: float = 0.25):
        self.threshold = threshold
        self.window = window
        self.min_samples = max(1, int(min_samples))
        self.alpha = alpha

        self.history = defaultdict(lambda: deque(maxlen=500))
        self.ema = {}
        self.last_seen = {}

    def remove_tokens(self, active_tokens):
        """Libera o histórico de tokens que saíram dos mercados monitorados."""
        active = set(active_tokens)
        for token_id in list(self.last_seen):
            if token_id not in active:
                self.last_seen.pop(token_id, None)
                self.history.pop(token_id, None)
                self.ema.pop(token_id, None)

    def add_price(self, token_id: str, price: float):
        if not math.isfinite(price) or not 0 < price < 1:
            return None

        now = time.monotonic()
        last = self.last_seen.get(token_id)

        if last is not None and (now - last) > self.window:
            self.history.pop(token_id, None)
            self.ema.pop(token_id, None)

        self.last_seen[token_id] = now

        dq = self.history[token_id]
        while dq and (now - dq[0][0]) > self.window:
            dq.popleft()

        previous_ema = self.ema.get(token_id)
        dq.append((now, price))

        if previous_ema is None:
            self.ema[token_id] = price
            return None

        self.ema[token_id] = (self.alpha * price) + ((1.0 - self.alpha) * previous_ema)

        if len(dq) < self.min_samples or previous_ema <= 0:
            return None

        # Exige que os últimos trades não estejam em direções opostas anulando o movimento.
        if len(dq) >= 2:
            ult_delta = dq[-1][1] - dq[-2][1]
            if (price > previous_ema and ult_delta < 0) or (price < previous_ema and ult_delta > 0):
                return None

        pct = (price - previous_ema) / previous_ema

        if abs(pct) >= self.threshold:
            event_type = "SPIKE_UP" if pct > 0 else "SPIKE_DOWN"
            self.ema[token_id] = price
            return (event_type, previous_ema, price, pct)

        return None
