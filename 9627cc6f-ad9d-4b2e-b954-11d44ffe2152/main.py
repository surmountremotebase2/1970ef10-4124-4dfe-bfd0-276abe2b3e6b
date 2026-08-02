from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log


class TradingStrategy(Strategy):
    # Hold SOXL overnight (buy at close, sell next open), flat during the
    # session, only when 5 of 7 index ETFs are above their 200-day average.
    # Surmount's 5-min buffer is only ~250 bars, so daily closes are
    # accumulated here as the backtest runs. First ~200 sessions are warmup.

    def __init__(self):
        self.trade_ticker = "SOXL"
        self.breadth_universe = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XBI", "SOXX"]
        self.breadth_min_count = 5
        self.sma_length = 200

        self.daily = {}          # {ticker: {session: close}}
        self.last_session = None
        self.holding = False
        self.logged = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        out = [self.trade_ticker]
        for t in self.breadth_universe:
            if t not in out:
                out.append(t)
        return out

    def _stamp(self, bar, ticker):
        d = bar[ticker]
        return str(d.get("date") or d.get("datetime") or "")

    def _record(self, ohlcv):
        # Keep the latest close for each ticker for each session.
        bar = ohlcv[-1]
        for t in self.assets:
            if t not in bar:
                continue
            s = self._stamp(bar, t)
            if len(s) < 10:
                continue
            self.daily.setdefault(t, {})[s[:10]] = bar[t]["close"]

    def _breadth(self):
        count = 0
        ready = 0
        for t in self.breadth_universe:
            hist = self.daily.get(t, {})
            if len(hist) < self.sma_length + 1:
                continue
            keys = sorted(hist.keys())[:-1]          # exclude today
            if len(keys) < self.sma_length:
                continue
            vals = [hist[k] for k in keys[-self.sma_length:]]
            if hist[keys[-1]] > sum(vals) / float(len(vals)):
                count += 1
            ready += 1
        return count, ready

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return TargetAllocation({})

        self._record(ohlcv)

        bar = ohlcv[-1]
        if self.trade_ticker not in bar:
            return TargetAllocation({self.trade_ticker: 1.0 if self.holding else 0.0})

        stamp = self._stamp(bar, self.trade_ticker)
        session = stamp[:10]
        clock = stamp[11:16] if len(stamp) >= 16 else ""

        if not self.logged:
            log("SETUP: accumulating daily closes; needs %d sessions before trading"
                % (self.sma_length + 1))
            self.logged = True

        # New session means the overnight hold is finished -> sell.
        if self.last_session is not None and session != self.last_session:
            if self.holding:
                log("EXIT: %s at %s on %s" % (self.trade_ticker, clock, session))
                self.holding = False
        self.last_session = session

        # Only act on the last bar of the session.
        if clock < "15:55":
            return TargetAllocation({self.trade_ticker: 1.0 if self.holding else 0.0})

        count, ready = self._breadth()

        if ready < len(self.breadth_universe):
            have = len(self.daily.get(self.breadth_universe[0], {}))
            log("WARMUP: %d/%d tickers ready, %d sessions collected"
                % (ready, len(self.breadth_universe), have))
            self.holding = False
            return TargetAllocation({self.trade_ticker: 0.0})

        if count >= self.breadth_min_count:
            if not self.holding:
                log("ENTRY: %s close %s | breadth %d/7" % (self.trade_ticker, session, count))
            self.holding = True
            return TargetAllocation({self.trade_ticker: 1.0})

        log("GATE OFF: breadth %d/7 on %s" % (count, session))
        self.holding = False
        return TargetAllocation({self.trade_ticker: 0.0})