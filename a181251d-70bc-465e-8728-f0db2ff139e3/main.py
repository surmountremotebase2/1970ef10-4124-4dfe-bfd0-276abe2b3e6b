from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np


class TradingStrategy(Strategy):

    def __init__(self):
        self.tickers = ["TECL", "SOXL", "AGQ", "UCO", "GDXU"]

        self.allocation_size = 0.50   # per position
        self.max_positions = 2
        self.vwap_len = 12            # bars, = 1 hour
        self.rvol_threshold = 1.8

        # --- EXITS, PER TICKER -------------------------------------
        # Each ticker tested ALONE across three independent years, then
        # confirmed together in the full two-slot book.
        #
        #   ticker   TP / stop   note
        #   SOXL      35% /  8%  the engine -- remove it and 3yr goes 4.79x -> 1.80x
        #   AGQ       30% / 12%
        #   GDXU      20% / 20%  wide stop is deliberate: 7.22% daily sigma
        #   TECL      10% /  4%
        #   UCO        3% /  8%  see below
        #
        # UCO's 3%/8% LOSES MONEY ON ITS OWN TRADES and is still correct.
        # Measured: 70% win rate against a 73% break-even, avg win +3.84%,
        # avg loss -8.31%, win/loss 0.46 -- the only ticker below 1.6.
        # It is not here to earn. It is a FAST-CYCLING PLACEHOLDER: it
        # takes a slot, which denies that slot to a second tech or second
        # metals position, then exits within days so a better instrument
        # can have it. Every setting with sounder arithmetic makes it hold
        # LONGER and makes the book WORSE:
        #
        #   UCO  3% / 8%  ->  12.92x   -44.9%   <- shipped
        #   UCO  6% / 6%  ->   8.85x   -42.3%
        #   UCO  6% / 8%  ->   7.08x   -46.2%
        #   UCO  7% / 6%  ->   5.15x   -41.4%   (best arithmetic, worst book)
        #   no UCO at all ->   8.25x   -59.7%   (worse on BOTH axes)
        self.take_profits = {
            "TECL": 0.10,
            "SOXL": 0.35,
            "AGQ":  0.30,
            "UCO":  0.03,
            "GDXU": 0.20,
        }
        self.trailing_stops = {
            "TECL": 0.04,
            "SOXL": 0.08,
            "AGQ":  0.12,
            "UCO":  0.08,
            "GDXU": 0.20,
        }
        # fallback for any ticker missing from the dicts above
        self.take_profit_pct = 0.25
        self.trailing_stop_pct = 0.12

        # {"TICKER": {"entry_price": X, "peak_price": Y, "weight": W}}
        # weight drifts with P&L rather than being reset, so the platform
        # is handed the position's real weight and places no needless trade
        self.active_positions = {}
        self.exited_tickers = []

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def take_profit_for(self, ticker):
        return self.take_profits.get(ticker, self.take_profit_pct)

    def trailing_stop_for(self, ticker):
        return self.trailing_stops.get(ticker, self.trailing_stop_pct)

    def get_conviction_score(self, history):
        if len(history) < 200:
            return 0
        df = pd.DataFrame(history)

        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df["close"] * recent_df["volume"]).sum() / recent_df["volume"].sum()
        current_price = df["close"].iloc[-1]

        avg_vol = df["volume"].tail(20).mean()
        rvol = df["volume"].iloc[-1] / avg_vol if avg_vol > 0 else 0

        sma_macro = df["close"].tail(200).mean()

        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def _observed_weight(self, ticker, holdings):
        """The position's ACTUAL weight now, as the platform reports it."""
        if not holdings:
            return None
        raw = holdings.get(ticker, None)
        if raw is None:
            return None
        try:
            w = float(raw)
        except (TypeError, ValueError):
            return None
        return w if 0.0 < w <= 1.0 else None

    def run(self, data):
        d = data.get("ohlcv")
        if not d:
            return None

        holdings = data.get("holdings", {})
        self.exited_tickers = []
        state_changed = False
        newly_entered = set()

        # --- bookkeeping only: record real weights, place no trades ---
        for t in self.active_positions:
            observed = self._observed_weight(t, holdings)
            if observed is not None:
                self.active_positions[t]["weight"] = observed

        # --- 1. manage what is held --------------------------------
        for t, m in list(self.active_positions.items()):
            bar = d[-1].get(t)
            if not bar:
                continue
            cp = bar["close"]

            if cp > m["peak_price"]:
                self.active_positions[t]["peak_price"] = cp

            tp = self.take_profit_for(t)
            if cp >= m["entry_price"] * (1 + tp):
                log(f"TAKE PROFIT ({tp:.0%}): {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

            st = self.trailing_stop_for(t)
            if cp <= m["peak_price"] * (1 - st):
                log(f"SWING STOP ({st:.0%}): {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

        # --- 2. pick at most one new position ----------------------
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                if t in self.active_positions:
                    continue
                hist = [b[t] for b in d if t in b]
                if hist:
                    sc = self.get_conviction_score(hist)
                    if sc > 0:
                        scores[t] = sc

            if scores:
                best = max(scores, key=scores.get)
                self.active_positions[best] = {
                    "entry_price": d[-1][best]["close"],
                    "peak_price": d[-1][best]["close"],
                    "weight": self.allocation_size,
                }
                newly_entered.add(best)
                state_changed = True
                log(f"SWING ENTRY (50%): {best} | RVOL: {scores[best]:.2f}")

        # --- 3. submit the book ------------------------------------
        # A new position gets allocation_size. Existing ones are submitted
        # at the weight they have actually drifted to, so the platform sees
        # no difference and trades nothing. Resetting every position to 50%
        # on any change sells down winners and tops up losers.
        if state_changed:
            alloc = {}
            for t, m in self.active_positions.items():
                alloc[t] = (self.allocation_size if t in newly_entered
                            else m.get("weight", self.allocation_size))
            total = sum(alloc.values())
            if total > 1.0:
                alloc = {t: w / total for t, w in alloc.items()}
            log("ALLOC: " + ", ".join(f"{t} {w:.1%}" for t, w in alloc.items()))
            return TargetAllocation(alloc)

        return None