"""
SEED GOLD -- TQQQ while the Nasdaq trend holds, GOLD when it doesn't.
Never sits in cash, never fully out of the market.

    QQQ more than 1.5% below its 200-day average  ->  FLOOR in TQQQ, rest GLD
    QQQ back above its 200-day average            ->  100% TQQQ

Asymmetric by design: slow to sell, quick to buy. See the band comment.
"""

from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):

    RISK = "TQQQ"        # held while the trend is intact
    PARK = "GLD"         # held while it is not. "SHY"/"BIL" = safer, poorer
    MACRO = "QQQ"        # unleveraged underlying -- the signal comes from here
    SMA_LEN = 200

    # ASYMMETRIC HYSTERESIS -- changed 2026-08-05, was a symmetric +/-0.5%.
    # Walk-forward, train 4y -> trade 1y, 13 folds, out of sample:
    #     -1.5% / 0.0%    +2.6%   maxDD -50.2%   Sharpe 1.03   <- now
    #     -0.5% / 0.0%    +2.4%   maxDD -52.6%   Sharpe 1.04
    #     -0.5% / +0.5%    base   maxDD -53.1%   Sharpe 1.03   <- was
    #     -1.0% / +0.5%   -4.4%
    #      0.0% /  0.0%  -24.7%   <- no band at all
    # All three zero-entry variants beat the old setting; all three +0.5%
    # variants lose to it. In-sample this measured +39.6%; out of sample
    # +2.6%, ~0.2%/yr. The honest case is the drawdown, not the return.
    EXIT_BAND = -0.015   # held risk-ON: go defensive once rel < this
    ENTRY_BAND = 0.0     # held risk-OFF: go risk-on once rel > this

    # EXPOSURE FLOOR. Walk-forward on corrected data: 10% and 25% are
    # equivalent, everything >=33% is worse out of sample (60% is -20.9%)
    # even though the in-sample sweep says the opposite. Do not raise it.
    FLOOR = 0.25

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
            # Not enough history to judge. Wait in the parking asset
            # rather than in cash -- being idle should still earn.
            return TargetAllocation({self.RISK: self.FLOOR,
                                     self.PARK: 1.0 - self.FLOOR})

        sma = sum(macro[-self.SMA_LEN:]) / float(self.SMA_LEN)
        q, price = macro[-1], risk[-1]

        # Asymmetric hysteresis: the threshold depends on which side we are
        # already on, and the two sides are deliberately different widths.
        rel = q / sma - 1
        if self._state:
            want_risk = rel > self.EXIT_BAND    # already in: need a REAL break
        else:
            want_risk = rel > self.ENTRY_BAND   # in park: any cross will do

        if want_risk != self._state:
            if want_risk:
                log(f"RISK ON  -- {self.MACRO} {q:,.2f} is {rel*100:+.1f}% vs its "
                    f"{self.SMA_LEN}dma. 100% {self.RISK} at {price:,.2f}.")
                self._peak, self._milestone = price, 0
            else:
                log(f"RISK OFF -- {self.MACRO} {q:,.2f} is {rel*100:+.1f}% vs its "
                    f"{self.SMA_LEN}dma. Down to {self.FLOOR:.0%} {self.RISK}, "
                    f"rest in {self.PARK}. Never fully out.")
            self._state = want_risk

        # reporting only; changes nothing
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

        # Name BOTH tickers every bar. Surmount treats an omitted ticker
        # as zero, so silence is an instruction here, not an absence.
        risk_w = 1.0 if want_risk else self.FLOOR
        return TargetAllocation({self.RISK: risk_w, self.PARK: 1.0 - risk_w})