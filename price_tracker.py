import time
from collections import defaultdict, deque

class PriceTracker:
    def __init__(self, threshold: float, window: int):
        self.threshold = threshold
        self.window = window
        self.history = defaultdict(deque)

    def add_price(self, token_id: str, price: float):
        now = time.time()
        dq = self.history[token_id]
        dq.append((now, price))

        while dq and now - dq[0][0] > self.window:
            dq.popleft()

        if len(dq) < 2:
            return None

        old_ts, old_price = dq[0]
        if old_price == 0:
            return None

        pct = (price - old_price) / old_price
        if abs(pct) >= self.threshold:
            dq.clear()
            return (old_price, pct)
        return None