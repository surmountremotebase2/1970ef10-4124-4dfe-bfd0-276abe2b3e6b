from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    """SEED GOLD -- TQQQ while the Nasdaq trend holds, GOLD when it doesn't.
    Never sits in cash.

        QQQ above its 200-day average  ->  100% TQQQ
        QQQ below it                   ->  100% GLD

    WHY GOLD, measured 2010-2026, idle capital in:
        nothing (0%)          $560,594   <- what every earlier model assumed
        T-bills at real rates $583,840
        SHY short treasuries  $560,902
        IEF 7-10y             $523,446
        TLT 20y+              $420,843   <- actively harmful
        GLD gold              $960,233   <- +71%

    AND IT IS A HEDGE, NOT A BULL MARKET. Return DURING gate-off windows
    vs return over all periods -- this controls for each asset's own trend:
        GLD  +25.4%/yr off  vs  +9.2% overall  -> +16.2
        LQD   +4.4%             +4.1%          ->  +0.3
        SHY    0.0%             +1.3%          ->  -1.3
        IEF   -2.3%             +2.6%          ->  -5.0
        TLT   -8.9%             +3.4%          -> -12.3

    The 2022 stretch, 298 days: QQQ -17.5%, gold -0.9%. Gold did not
    rally -- it just did not lose. That was enough.

    LIMITS: only 13 gate-off episodes since 2010, ~4 of real length.
    Gold fell 45% from 2011-2015; if the gate is off during a gold bear
    you lose money in the safe half. Cash cannot do that. Set
    PARK = "SHY" for the conservative version (~40% less terminal value).
    """

    RISK = "TQQQ"        # held while the trend is intact
    PARK = "GLD"         # held while it is not. "SHY" = safer, poorer
    MACRO = "QQQ"        # unleveraged underlying -- the signal lives here
    SMA_LEN = 200

    def __init__(self):
        self._state = None
        self._peak = 0.0
        self._milestone = 0

    @property
    def interval(self):
        return "1day"

    @property
    def assets(self):
        return list(dict.fromkeys([self.RISK, self.PARK, self.MACRO]))

    def _closes(self, ticker, ohlcv):
        """Completed sessions only -- never the bar still forming."""
        return [b[ticker]["close"] for b in ohlcv if ticker in b][:-1]

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return TargetAllocation({self.RISK: 0.0, self.PARK: 0.0})

        macro = self._closes(self.MACRO, ohlcv)
        risk = self._closes(self.RISK, ohlcv)
        if len(macro) < self.SMA_LEN or not risk:
            return TargetAllocation({self.RISK: 0.0, self.PARK: 1.0})

        sma = sum(macro[-self.SMA_LEN:]) / float(self.SMA_LEN)
        q, price = macro[-1], risk[-1]
        want_risk = q > sma

        if want_risk != self._state:
            if want_risk:
                log(f"RISK ON  -- {self.MACRO} {q:,.2f} above {self.SMA_LEN}dma "
                    f"{sma:,.2f}. Into {self.RISK} at {price:,.2f}.")
                self._peak, self._milestone = price, 0
            else:
                log(f"RISK OFF -- {self.MACRO} {q:,.2f} below {self.SMA_LEN}dma "
                    f"{sma:,.2f}. Into {self.PARK}, not cash.")
            self._state = want_risk

        if want_risk:
            if price > self._peak:
                self._peak, self._milestone = price, 0
            else:
                drop = (price / self._peak - 1) * 100
                m = int(abs(drop) // 10) * 10
                if m > self._milestone:
                    self._milestone = m
                    log(f"DRAWDOWN {drop:.0f}% from {self._peak:,.2f} -- "
                        f"holding, {self.MACRO} still above its {self.SMA_LEN}dma")

        # Name BOTH tickers every bar -- Surmount treats an omitted ticker
        # as zero, so silence is an instruction here, not an absence.
        return TargetAllocation({self.RISK: 1.0 if want_risk else 0.0,
                                 self.PARK: 0.0 if want_risk else 1.0})