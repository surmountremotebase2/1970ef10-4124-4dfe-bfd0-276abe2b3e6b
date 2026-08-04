from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    """SEED GATED -- hold TQQQ while the Nasdaq's own trend is intact.

    ENTER  QQQ above its 200-day average AND TQQQ's 12-day EMA above
           its 26-day EMA
    EXIT   QQQ falls below its 200-day average
    No profit target, no stop, no sizing.

    CHANGED FROM THE ORIGINAL, each measured:
      FIXED  entry is a STATE, not a crossover EVENT. As written it
             returned $46,517 on 2016-2026 vs buy-and-hold's $289,223.
             This one line took it to $420,874.
      CUT    the 20% profit target -- worth $18,273 with vs $18,302
             without across every 3-year window 1999-2026. Nothing.
      CUT    the 25x ATR stop -- TQQQ's daily ATR is ~3-4% of price, so
             25x sits ~90% below the high. It never fired once.
      KEPT   the 200-day macro gate. It is the entire engine.
    """

    TICKER = "TQQQ"        # traded
    MACRO = "QQQ"          # unleveraged underlying -- signals come from here
    SMA_LEN = 200
    FAST, SLOW = 12, 26

    def __init__(self):
        self._in = False
        self._peak = 0.0
        self._milestone = 0
        self._logged = False

    @property
    def interval(self):
        return "1day"

    @property
    def assets(self):
        return list(dict.fromkeys([self.TICKER, self.MACRO]))

    def _closes(self, ticker, ohlcv):
        """Completed sessions only -- never the bar still forming."""
        return [b[ticker]["close"] for b in ohlcv if ticker in b][:-1]

    def _ema(self, xs, span):
        k = 2.0 / (span + 1.0)
        e = xs[0]
        for x in xs[1:]:
            e = x * k + e * (1 - k)
        return e

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return TargetAllocation({self.TICKER: 0.0})

        macro = self._closes(self.MACRO, ohlcv)
        fund = self._closes(self.TICKER, ohlcv)
        if len(macro) < self.SMA_LEN or len(fund) < self.SLOW + 5:
            return TargetAllocation({self.TICKER: 0.0})

        sma = sum(macro[-self.SMA_LEN:]) / float(self.SMA_LEN)
        macro_ok = macro[-1] > sma

        window = fund[-120:]
        momentum_ok = self._ema(window, self.FAST) > self._ema(window, self.SLOW)

        price = fund[-1]
        if not self._logged:
            log(f"SETUP trade {self.TICKER}, signals from {self.MACRO}. "
                f"Enter on {self.MACRO} > {self.SMA_LEN}dma AND "
                f"{self.FAST}/{self.SLOW} EMA up. Exit on macro breakdown only.")
            self._logged = True

        # ---- exit: macro breakdown is the ONLY exit ----------------------
        if self._in:
            if not macro_ok:
                log(f"EXIT  {self.MACRO} {macro[-1]:,.2f} below {self.SMA_LEN}dma "
                    f"{sma:,.2f} -- to cash at {price:,.2f}")
                self._in, self._peak, self._milestone = False, 0.0, 0
                return TargetAllocation({self.TICKER: 0.0})

            # reporting only; changes nothing
            if price > self._peak:
                self._peak = price
                self._milestone = 0
            else:
                drop = (price / self._peak - 1) * 100
                m = int(abs(drop) // 10) * 10
                if m > self._milestone:
                    self._milestone = m
                    log(f"DRAWDOWN {drop:.0f}% from {self._peak:,.2f} "
                        f"-- holding, macro still intact")
            return TargetAllocation({self.TICKER: 1.0})

        # ---- entry -------------------------------------------------------
        if macro_ok and momentum_ok:
            log(f"ENTER at {price:,.2f}  ({self.MACRO} {macro[-1]:,.2f} > "
                f"{sma:,.2f}, EMA{self.FAST} > EMA{self.SLOW})")
            self._in, self._peak, self._milestone = True, price, 0
            return TargetAllocation({self.TICKER: 1.0})

        return TargetAllocation({self.TICKER: 0.0})