import math
import time
from collections import defaultdict, deque


class PriceTracker:
    def __init__(self, threshold: float, window: int):
        self.threshold = threshold
        self.window = window

        self.history = defaultdict(lambda: deque(maxlen=500))
        self.ema = {}
        self.last_seen = {}
        self.alpha = 0.2

    def add_price(self, token_id: str, price: float):
        if not math.isfinite(price) or not 0 < price < 1:
            return None

        now = time.monotonic()

        if now - self.last_seen.get(token_id, now) > self.window:
            self.history.pop(token_id, None)
            self.ema.pop(token_id, None)

        self.last_seen[token_id] = now

        dq = self.history[token_id]

        while dq and now - dq[0][0] > self.window:
            dq.popleft()

        previous_ema = self.ema.get(token_id, price)

        dq.append((now, price))

        self.ema[token_id] = (
            self.alpha * price
            + (1 - self.alpha) * previous_ema
        )

        if len(dq) < 5 or previous_ema <= 0:
            return None

        pct = (price - previous_ema) / previous_ema

        # Este bot alerta somente altas.
        if pct >= self.threshold:
            self.ema[token_id] = price
            dq.clear()
            dq.append((now, price))
            return ("SPIKE", previous_ema, price, pct)

        return None