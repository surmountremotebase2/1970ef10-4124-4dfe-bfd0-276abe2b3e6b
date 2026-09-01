from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np


class TradingStrategy(Strategy):

    def __init__(self):
        self.tickers = ["TECL", "SOXL", "AGQ", "UCO", "GDXU"]

        self.allocation_size = 0.33
        self.max_positions = 3
        self.vwap_len = 12
        self.rvol_threshold = 1.8

        # --- EXITS, PER TICKER -------------------------------------
        # GDXU changed from 20/20 to 25/12 on 2026-09-01.
        # Across six years in the full book, every 12% stop variant beat
        # the old 20/20 on compounded growth AND turned both losing years
        # positive. 25/12 was best: 19.44x vs 12.73x, worst year +20.9%
        # against -9.3%.
        #
        # WHAT THIS COSTS: GDXU tested ALONE strongly prefers the wide
        # stop over 1 and 3 years (+148% vs +48%). The tight stop only
        # wins inside the book, where an early exit frees the slot for
        # another ticker instead of sitting in cash. In a pure metals
        # melt-up this WILL leave money on the table.
        self.take_profits = {
            "TECL": 0.10,
            "SOXL": 0.35,
            "AGQ":  0.30,
            "UCO":  0.03,
            "GDXU": 0.25,
        }
        self.trailing_stops = {
            "TECL": 0.04,
            "SOXL": 0.08,
            "AGQ":  0.12,
            "UCO":  0.08,
            "GDXU": 0.12,
        }
        self.take_profit_pct = 0.25     # fallback
        self.trailing_stop_pct = 0.12   # fallback

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

        for t in self.active_positions:
            observed = self._observed_weight(t, holdings)
            if observed is not None:
                self.active_positions[t]["weight"] = observed

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
                log(f"SWING ENTRY (33%): {best} | RVOL: {scores[best]:.2f}")

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