from surmount.base_class import Strategy, TargetAllocation


class TradingStrategy(Strategy):
    # CONTROL TEST: hold SOXL 100% of the time, always. No logic.
    # Over 2022-08-02 to 2026-08-02, SOXL buy-and-hold returned +492.6%.
    # If Surmount reports roughly that, its execution is fair and the
    # discrepancy lies elsewhere. If it reports far less, the platform is
    # applying implicit costs and every backtest run on it is understated.

    def __init__(self):
        self.ticker = "SOXL"

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return [self.ticker]

    def run(self, data):
        return TargetAllocation({self.ticker: 1.0})