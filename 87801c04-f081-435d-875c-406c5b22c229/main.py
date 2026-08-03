from surmount.base_class import Strategy, TargetAllocation


class TradingStrategy(Strategy):
    # CONTROL: hold SOXL 50% and TECL 50%, never trade. No logic.
    # Over 2023-08-02 to 2026-08-02 the correct answer is a 50/50
    # buy-and-hold of the two, which I can compute exactly.
    # If Surmount reports materially more, the discrepancy is in how it
    # accounts for multi-position portfolios -- which would explain
    # everything we've been unable to pin down.

    def __init__(self):
        self.tickers = ["SOXL", "TECL"]

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        return TargetAllocation({"SOXL": 0.50, "TECL": 0.50})