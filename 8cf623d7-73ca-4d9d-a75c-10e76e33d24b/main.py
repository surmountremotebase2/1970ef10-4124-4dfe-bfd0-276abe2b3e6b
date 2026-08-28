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
        self.take_profit_pct = 0.25     # fallback
        self.trailing_stop_pct = 0.12   # fallback

        # --- THEME GUARD: never hold two of the same theme ---------
        # Measured, three independent years, full book:
        #   guard off              12.92x   -44.9%   589 trades
        #   selection only          7.38x   -44.9%   618   <- fails
        #   BLOCK same theme       12.27x   -36.6%   676   <- this
        #
        # "Selection only" still took a same-theme second position when
        # nothing fresh was available -- it just picked the LOWER-RVOL
        # name. All of the concentration risk, none of the better
        # instrument. Blocking outright is what actually works, and it
        # TRADES MORE, not less: leaving the slot empty frees it to catch
        # a fresh-theme signal shortly after instead of being stuck in a
        # duplicate position.
        self.themes = {"SOXL": "TECH", "TECL": "TECH",
                       "AGQ": "METAL", "GDXU": "METAL", "GDX": "METAL",
                       "UCO": "OIL"}
        self.prefer_fresh_theme = True
        self.same_theme_size = 0.00

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
                held = {self.themes.get(t) for t in self.active_positions}
                fresh = {t: s for t, s in scores.items()
                         if self.themes.get(t) not in held}

                if not self.prefer_fresh_theme:
                    # guard OFF -- global best at full size (the control)
                    best, size, tag = (max(scores, key=scores.get),
                                       self.allocation_size, "SWING ENTRY")
                elif fresh:
                    best, size, tag = (max(fresh, key=fresh.get),
                                       self.allocation_size, "SWING ENTRY")
                elif self.same_theme_size > 0:
                    best, size, tag = (max(scores, key=scores.get),
                                       self.same_theme_size,
                                       "SWING ENTRY (same theme, reduced)")
                else:
                    best = None      # blocked: leave the slot empty

                if best:
                    self.active_positions[best] = {
                        "entry_price": d[-1][best]["close"],
                        "peak_price": d[-1][best]["close"],
                        "weight": size,
                    }
                    newly_entered.add(best)
                    state_changed = True
                    log(f"{tag} ({size:.0%}): {best} | RVOL: {scores[best]:.2f}")

        # --- 3. submit the book ------------------------------------
        if state_changed:
            alloc = {}
            for t, m in self.active_positions.items():
                alloc[t] = m.get("weight", self.allocation_size)
            total = sum(alloc.values())
            if total > 1.0:
                alloc = {t: w / total for t, w in alloc.items()}
            log("ALLOC: " + ", ".join(f"{t} {w:.1%}" for t, w in alloc.items()))
            return TargetAllocation(alloc)

        return None