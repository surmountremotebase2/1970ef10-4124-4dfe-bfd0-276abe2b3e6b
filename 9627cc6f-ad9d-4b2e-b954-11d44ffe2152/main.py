from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd


class TradingStrategy(Strategy):
    """
    OVERNIGHT + BREADTH strategy.

    THE IDEA IN ONE SENTENCE
    ------------------------
    These instruments make their money while the market is CLOSED, so
    hold overnight and stay flat during the trading session -- but only
    when the broad market is healthy.

    WHY (measured, not assumed)
    ---------------------------
    Splitting returns into overnight (close->open) and intraday
    (open->close) across 7 tickers and 17 years: overnight beat intraday
    on ALL SEVEN, and 6 of 7 had NEGATIVE intraday returns. SOXL over
    16.4 years: overnight-only +62% CAGR, intraday-only -15% CAGR.
    Confirmed three separate ways, including 10.6 years of 5-minute
    bars where every profitable configuration held overnight and every
    flat-by-close configuration made essentially nothing.

    THE GATE
    --------
    Hold only when at least 5 of 7 major index ETFs are above their own
    200-day average. This improved BOTH return and drawdown on 6 of 6
    leveraged ETFs across unrelated sectors with no refitting -- the
    only filter tested that generalized that cleanly.

    WHAT IS DELIBERATELY ABSENT
    ---------------------------
    No RVOL, MACD, VWMA, profit target, trailing stop, or conviction
    score. All were tested. On 10.6 years of 5-minute data the intraday
    indicator stack added nothing -- it was selecting when to open a
    position that got paid overnight. Adaptive parameter selection lost
    in all three experiments where it was tried. The simplicity is the
    result, not a shortcut.

    EXECUTION ON SURMOUNT
    ---------------------
    Surmount can hold overnight but cannot trade outside regular hours.
    That is fine: this buys on the last bar of the session and sells on
    the first bar of the next. Both are regular-hours trades. Tested
    against idealized close->open execution on 10.6 years of 5-minute
    bars: identical results (68.5% CAGR either way on SOXL).

    IMPORTANT: set realistic slippage and fees in the backtest. This
    trades ~190 nights/year. At 0.05%/round trip SOXL returns ~38.7%;
    at 0.20% it returns ~5%. The edge lives inside the cost assumption.
    """

    def __init__(self):
        # The instrument actually traded. SOXL had the strongest overnight
        # premium of the six leveraged ETFs tested -- the premium scales
        # with the UNDERLYING's volatility, not the leverage multiple
        # (SOXX 31% vol -> 41.7% overnight CAGR; SPY 17% vol -> 6.5%,
        # both at 3x). Alternatives that also validated: TQQQ, TECL, TNA.
        self.trade_ticker = "SOXL"

        # Market-breadth universe. Deliberately broad -- this measures
        # whether the WHOLE market is healthy, not just semiconductors.
        self.breadth_universe = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XBI", "SOXX"]

        # At least 5 of 7 above their 200-day average.
        # Tested: 3 of 7 (Sharpe 0.73), 4 of 7 (0.82), 5 of 7 (0.91),
        # 6 of 7 (0.70). Five is the peak and the shape is sensible --
        # more breadth is better until demanding near-unanimity costs
        # too many nights.