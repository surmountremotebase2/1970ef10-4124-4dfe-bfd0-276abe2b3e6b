"""
SEED GOLD -- TQQQ while the Nasdaq trend holds, GOLD when it doesn't.
Never sits in cash, never fully out of the market.

    QQQ above its 200-day average (by BAND)  ->  100% TQQQ
    QQQ below it (by BAND)                   ->  FLOOR in TQQQ, rest GLD

--------------------------------------------------------------------------
MEASURED, split-adjusted data, $100,000

    trailing 1 year    engine $146,271    buy & hold $147,668
    trailing 3 years   engine $335,835    buy & hold $314,001
    trailing 5 years   engine $361,348    buy & hold $190,419
    2016-2026          engine $5,336,815  (48.9%/yr, -53.1%, Sharpe 1.06)

WALK-FORWARD, 12 folds, train 4y trade 1y, fully out of sample:
    this engine    $7,929,609   44.0%/yr   -60.9%   Sharpe 0.99
    always 100%    $4,316,209   36.9%/yr   -82.5%   Sharpe 0.82

The edge lives in bear markets. Over five years it beat buy-and-hold by
$170,929 -- and essentially ALL of it came from 2022. In bull years it
roughly keeps pace. That is the product, not a flaw in it.

--------------------------------------------------------------------------
WHY GOLD AND NOT CASH OR BONDS -- measured 2010-2026, idle capital in:

    nothing (0%)          $560,594     <- what every earlier model assumed
    T-bills at real rates $583,840
    SHY short treasuries  $560,902
    IEF 7-10y             $523,446
    TLT 20y+              $420,843     <- actively harmful
    GLD gold              $960,233     <- +71%

AND IT IS A HEDGE, NOT A BULL MARKET. Each vehicle's return DURING
gate-off windows vs its return over all periods -- this controls for the
asset's own trend:

    GLD  +25.4%/yr when gate is off  vs  +9.2% overall  -> +16.2
    LQD   +4.4%                          +4.1%          ->  +0.3
    SHY    0.0%                          +1.3%          ->  -1.3
    TLT   -8.9%                          +3.4%          -> -12.3

Gold does better precisely when the gate fires; treasuries do WORSE than
usual precisely then. The mechanism is not mined: the gate fires on
equity stress, stress brings flight-to-safety and easier policy, gold is
bid on both. Bonds only work when the stress is DEFLATIONARY -- 2022 was
inflationary, which is why TLT fell 29.4% while gold was +0.8%.

The episode that matters, the 298-day 2022 stretch:
    QQQ -17.5%,  gold -0.9%.
Gold did not rally. It just did not lose. That was enough.

--------------------------------------------------------------------------
THINGS TRIED AND REJECTED -- do not re-add without new evidence

  conditional parking (GLD/IEF/SHY by trend)  my +14% figure had
      LOOKAHEAD -- it used today's trend reading to pick today's return.
      Implemented honestly it LOSES on all four windows.
  8% intraday trailing stop  real in simulation (walk-forward Sharpe
      1.17 vs 1.00, most stable parameters in the project) but a
      daily-bar strategy sees only YESTERDAY's low and exits a session
      late. Three implementations all landed ~3x below this file.
      Capturing it needs genuine intraday execution.
  asymmetric gate, 20% profit target, 50% floor, regime-conditional
      parameters, covered calls, momentum rotation, an 18-feature
      multifactor model, VIX guards, inverse ETFs, the overnight
      strategy -- all tested, all worse.

--------------------------------------------------------------------------
HONEST LIMITS

THIRTEEN EPISODES. The gate has been off in only 13 distinct stretches
since 2010, ~4 of real length. A large effect on a modest sample.

GOLD IS NOT RISK-FREE. It fell 45% from 2011-2015. Set PARK = "SHY" for
the timid version (~40% less terminal value).

IT DOES NOT PREVENT RUIN. A dot-com-scale grind still does severe damage.

IT LOSES OVER 2010-2026 ($24.5M vs buy-and-hold's $29.5M). That window
has no early bear market and five years of chop the gate paid for and
never collected on. Every result here is window-dependent.

IN A TAXABLE ACCOUNT it only wins if a bear market happens while you own
it. Over three years without one, the tax bill on short-term gains
erases the entire edge and $36,000 more.
"""

from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):

    RISK = "TQQQ"        # held while the trend is intact
    PARK = "GLD"         # held while it is not. "SHY" = safer, poorer
    MACRO = "QQQ"        # unleveraged underlying -- the signal lives here
    SMA_LEN = 200

    # HYSTERESIS BAND. Switch only once QQQ is BAND away from its average,
    # not the instant it crosses. Without it the gate flips on a penny --
    # the live log showed four one-day round trips in 2022 and twelve
    # flips in seven months of 2016.
    #   walk-forward, 12 folds OOS:
    #     no band $366,438 (Sharpe 0.86) | +/-0.5% $749,179 (0.99)
    #     +/-1% $534,561 (0.93)          | +/-5% $304,245 (0.82)
    # SIX OF SEVEN settings beat no band, so the benefit is robust. The
    # specific 0.5% is not -- expect nearer the 1% result in practice.
    BAND = 0.005

    # EXPOSURE FLOOR. When the gate says risk-off, hold this much TQQQ
    # anyway. Not for safety -- so you are never completely absent when a
    # rally starts. Early 2023 the binary version whipsawed three times in
    # six weeks at the exact bottom and missed the start of the biggest
    # rally in a decade.
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
            # Not enough history to judge. Wait in the parking asset
            # rather than in cash -- being idle should still earn.
            return TargetAllocation({self.RISK: self.FLOOR,
                                     self.PARK: 1.0 - self.FLOOR})

        sma = sum(macro[-self.SMA_LEN:]) / float(self.SMA_LEN)
        q, price = macro[-1], risk[-1]

        # Hysteresis: the threshold depends on which side we are already
        # on, so ordinary noise around the average cannot flip the switch.
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