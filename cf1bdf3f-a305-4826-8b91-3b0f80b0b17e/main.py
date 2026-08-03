from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    """Hold a leveraged fund, sized by volatility, only while the broad
    market is healthy. Daily bars, ~9 trades a year.

    Signals come from UNLEVERAGED indices, never the leveraged fund.
    Volatility sizing does the drawdown control -- nine entry filters
    were tested for that job and all nine failed. No profit target:
    fixed targets truncate the winners these instruments depend on.
    """

    TRADE_TICKER = "SOXL"
    SMA_LENGTH = 200          # daily bars, not 5-minute
    BREADTH_MIN = 0.70        # fraction of the universe above its own 200dma
    TARGET_VOL = 0.40         # measured -43.8% max drawdown
    VOL_LOOKBACK = 60
    ENTER_BAND = 0.01         # clear the average by 1% before buying
    EXIT_BAND = -0.01         # fall 1% below it before selling
    REBAL_BAND = 0.10         # ignore allocation changes smaller than this
    MAX_WEIGHT = 1.00

    BREADTH_UNIVERSE = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XBI", "SOXX"]

    def __init__(self):
        self._invested = False
        self._weight = 0.0
        self._logged = False

    @property
    def interval(self):
        return "1day"

    @property
    def assets(self):
        return list(dict.fromkeys([self.TRADE_TICKER] + self.BREADTH_UNIVERSE))

    def _closes(self, ticker, ohlcv):
        """Completed sessions only -- the final bar may still be forming."""
        return [b[ticker]["close"] for b in ohlcv if ticker in b][:-1]

    def _breadth(self, ohlcv):
        above = seen = 0
        for t in self.BREADTH_UNIVERSE:
            c = self._closes(t, ohlcv)
            if len(c) < self.SMA_LENGTH:
                continue
            sma = sum(c[-self.SMA_LENGTH:]) / float(self.SMA_LENGTH)
            above += 1 if c[-1] > sma else 0
            seen += 1
        return (above / seen if seen else 0.0), seen

    def _realised_vol(self, closes):
        w = closes[-(self.VOL_LOOKBACK + 1):]
        if len(w) < 20:
            return None
        rets = [w[i] / w[i - 1] - 1 for i in range(1, len(w)) if w[i - 1] > 0]
        if len(rets) < 15:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return (var ** 0.5) * (252 ** 0.5)

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return TargetAllocation({self.TRADE_TICKER: 0.0})

        fund = self._closes(self.TRADE_TICKER, ohlcv)
        breadth, seen = self._breadth(ohlcv)

        if seen < len(self.BREADTH_UNIVERSE) or len(fund) < self.VOL_LOOKBACK:
            return TargetAllocation({self.TRADE_TICKER: 0.0})

        if not self._logged:
            log(f"SETUP {self.TRADE_TICKER} | breadth over {seen} tickers | "
                f"target vol {self.TARGET_VOL:.0%}")
            self._logged = True

        threshold = (self.BREADTH_MIN + self.ENTER_BAND if not self._invested
                     else self.BREADTH_MIN + self.EXIT_BAND)
        invested = breadth >= threshold

        if not invested:
            if self._invested:
                log(f"EXIT  breadth {breadth:.0%} below {threshold:.0%}")
            self._invested, self._weight = False, 0.0
            return TargetAllocation({self.TRADE_TICKER: 0.0})

        vol = self._realised_vol(fund)
        if vol is None or vol <= 0:
            return TargetAllocation({self.TRADE_TICKER: self._weight})
        target = min(self.MAX_WEIGHT, self.TARGET_VOL / vol)

        if self._weight == 0.0 or abs(target - self._weight) >= self.REBAL_BAND:
            if not self._invested:
                log(f"ENTER breadth {breadth:.0%} | vol {vol:.0%} -> {target:.0%}")
            else:
                log(f"RESIZE {self._weight:.0%} -> {target:.0%} (vol {vol:.0%})")
            self._weight = target
        self._invested = True

        return TargetAllocation({self.TRADE_TICKER: self._weight})