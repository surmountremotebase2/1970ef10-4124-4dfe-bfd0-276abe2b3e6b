from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    """SEED 3-YEAR -- maximum expected money over a three-year horizon.

    Hold a 3x fund at 100%. No gate, no profit target, no volatility
    sizing, no exits. Every omission is a measured result.

    Across every 3-year window 1999-2026 (synthetic 3x Nasdaq):
        median          $10,000 -> $24,579
        reached $20,000  55% of windows
        ended below $10k 25% of windows
        worst window    $10,000 -> $10

    The 25% is not a warning, it is part of the product. If that trade
    is unacceptable, use strategy_seed_v1.py instead: 5% loss rate and a
    $6,168 worst case, costing 28% of the median.

    NO TREND GATE -- it works, but over 3 years it costs 28% of the
    median and 19 points of doubling probability. Over 15 years the gate
    is the right answer and this file is wrong.
    NO VOL TARGET -- swept 0.30 to unbounded, average weight 96-100% at
    every setting. It was doing nothing.
    NO PROFIT TARGET -- fixed targets truncate the winners these funds
    depend on. Trailing beat fixed 12 of 12 folds; no-exit beat both.
    ONE TRADE -- so nothing is taxed until sold, then at long-term rates.
    """

    # TQQQ = 3x Nasdaq 100. Won the longest window tested (2010-2026).
    # SOXL wins 2016-2026 but that is the AI/semiconductor boom, and it
    # is a single-industry bet at -90.5% drawdown. TECL is the middle.
    TICKER = "TQQQ"

    def __init__(self):
        self._peak = None
        self._last_milestone = 0
        self._entered = False

    @property
    def interval(self):
        return "1day"

    @property
    def assets(self):
        return [self.TICKER]

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return TargetAllocation({self.TICKER: 0.0})

        closes = [b[self.TICKER]["close"] for b in ohlcv if self.TICKER in b]
        if not closes:
            return TargetAllocation({self.TICKER: 1.0 if self._entered else 0.0})

        price = closes[-1]

        if not self._entered:
            log(f"ENTER {self.TICKER} at {price:,.2f} -- 100%, held, no exit "
                f"conditions. Expected: 55% chance of doubling in 3 years, "
                f"25% chance of ending down.")
            self._entered = True
            self._peak = price

        # ---- reporting only. Nothing below changes the allocation. ----
        if self._peak is None or price > self._peak:
            self._peak = price
            if self._last_milestone:
                log(f"RECOVERED to a new high at {price:,.2f}")
                self._last_milestone = 0
        else:
            drop = (price / self._peak - 1) * 100
            milestone = int(abs(drop) // 10) * 10
            if milestone > self._last_milestone:
                self._last_milestone = milestone
                log(f"DRAWDOWN {drop:.0f}% from peak {self._peak:,.2f} "
                    f"(now {price:,.2f}). Holding -- this strategy has no "
                    f"exit. For reference the worst 3-year window on record "
                    f"reached -99.9%.")

        return TargetAllocation({self.TICKER: 1.0})