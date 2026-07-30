from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    """
    BUY AND HOLD BENCHMARK — equal weight, bought once, never rebalanced.
    This is the number every active strategy must beat to justify existing.
    Weight is 0.95/5 to match the 5% cash buffer used in v5 and the null test.
    """

    def __init__(self):
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
        self.weight = 0.95 / len(self.tickers)

        self.invested = False
        self.entry_prices = {}
        self.bar_count = 0
        self.report_every = 390 # ~5 trading days at 5min bars

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def _latest_close(self, ticker, ohlcv):
        for row in reversed(ohlcv):
            if ticker in row:
                return float(row[ticker]["close"])
        return None

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return None

        self.bar_count += 1

        # --- one-time purchase ---
        if not self.invested:
            alloc = {}
            for t in self.tickers:
                px = self._latest_close(t, ohlcv)
                if px is None:
                    return None # wait until all tickers have data
                self.entry_prices[t] = px
                alloc[t] = float(self.weight)

            self.invested = True
            log(f"BUY AND HOLD: bought all {len(self.tickers)} tickers at "
                f"{self.weight:.2%} each")
            for t in self.tickers:
                log(f" entry {t} @ {self.entry_prices[t]}")
            return TargetAllocation(alloc)

        # --- periodic per-ticker performance report ---
        if self.bar_count % self.report_every == 0:
            parts = []
            for t in self.tickers:
                px = self._latest_close(t, ohlcv)
                if px and self.entry_prices.get(t):
                    r = (px / self.entry_prices[t] - 1) * 100
                    parts.append(f"{t} {r:+.1f}%")
            if parts:
                log("HOLD REPORT: " + " | ".join(parts))

        # --- never rebalance: this is what makes it true buy-and-hold ---
        return None