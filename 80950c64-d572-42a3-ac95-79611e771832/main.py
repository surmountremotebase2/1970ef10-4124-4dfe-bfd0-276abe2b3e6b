from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    """SEED GOLD -- TQQQ while the Nasdaq trend holds, GOLD when it doesn't.
    Never fully out, never in cash.

    WHY GOLD, measured 2010-2026, idle capital parked in:
        nothing (0%)          $560,594   <- what every earlier model assumed
        T-bills at real rates $583,840
        SHY short treasuries  $560,902
        TLT 20y+              $420,843   <- actively harmful
        GLD gold              $960,233   <- +71%

    AND IT IS A HEDGE, NOT A BULL MARKET. Return DURING gate-off windows
    vs return over all periods -- controls for each asset's own trend:
        GLD  +25.4%/yr off  vs  +9.2% overall  -> +16.2
        SHY    0.0%             +1.3%          ->  -1.3
        TLT   -8.9%             +3.4%          -> -12.3
    The 298-day 2022 stretch: QQQ -17.5%, gold -0.9%. Gold did not rally.
    It just did not lose. That was enough.

    LIMITS: only 13 gate-off episodes since 2010, ~4 of real length. Gold
    fell 45% from 2011-2015 -- if the gate is off during a gold bear you
    lose money in the safe half. Set PARK = "SHY" for the timid version.
    """

    RISK = "TQQQ"        # held while the trend is intact
    PARK = "GLD"         # held while it is not. "SHY"/"BIL" = safer, poorer
    MACRO = "QQQ"        # unleveraged underlying -- the signal lives here
    SMA_LEN = 200

    # HYSTERESIS BAND. Switch only once QQQ is BAND away from its average,
    # not the instant it crosses. Without it the gate flips on a penny --
    # the live log showed four one-day round trips in 2022 and twelve flips
    # in seven months of 2016.
    #   walk-forward, 12 folds OOS:  no band $366,438 (Sharpe 0.86)
    #   +/-0.5% $749,179 (0.99) | +/-1% $534,561 | +/-5% $304,245 (0.82)
    # SIX OF SEVEN settings beat no band, so the benefit is robust. The
    # specific 0.5% is not -- expect nearer the 1% result in practice.
    BAND = 0.005

    # EXPOSURE FLOOR. When the gate says risk-off, hold this much TQQQ
    # anyway. The point is not safety -- it is never being completely
    # absent when a rally starts. Early 2023 the binary version whipsawed
    # three times in six weeks at the exact bottom and missed the opening
    # of the biggest rally in a decade.
    #   walk-forward, 12 folds OOS, $100,000:
    #     floor 25%  $7,929,609  Sharpe 0.99   <- best
    #     floor  0%  $7,491,789  Sharpe 0.99
    #     floor 50%  $7,378,355  Sharpe 0.95
    #     always in  $4,316,209  Sharpe 0.82
    # IN-SAMPLE THIS LOOKED WORTH 80%. Out of sample it is worth 6%, and
    # the 50% floor that won in-sample came in BELOW no floor at all.
    # Keep it small. Do not raise it because a backtest says so.
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
            return TargetAllocation({self.RISK: self.FLOOR,
                                     self.PARK: 1.0 - self.FLOOR})

        sma = sum(macro[-self.SMA_LEN:]) / float(self.SMA_LEN)
        q, price = macro[-1], risk[-1]

        # Hysteresis: the threshold depends on which side we are already on,
        # so ordinary noise around the average cannot flip the switch.
        rel = q / sma - 1
        if self._state:
            want_risk = rel > -self.BAND       # already in: need a real break
        else:
            want_risk = rel > self.BAND        # in park: need real strength

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