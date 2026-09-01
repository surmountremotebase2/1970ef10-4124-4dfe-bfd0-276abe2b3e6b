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
        self.take_profit_pct = 0.25
        self.trailing_stop_pct = 0.12

        self.active_positions = {}
        self.exited_tickers = []

        # Bars during which a just-exited ticker may NOT be re-adopted by
        # amnesia recovery. Surmount keeps reporting a position in
        # `holdings` for at least two bars after the exit fills, so a
        # one-bar guard lets recovery buy back the very thing the stop
        # just sold -- observed 23 times in one year, same price, ten
        # minutes later. 12 bars is one hour.
        self.exit_cooldown = {}
        self.exit_cooldown_bars = 12

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

        # age the cooldown before recovery consults it
        for _t in list(self.exit_cooldown):
            self.exit_cooldown[_t] -= 1
            if self.exit_cooldown[_t] <= 0:
                del self.exit_cooldown[_t]

        # --- AMNESIA RECOVERY --------------------------------------
        # A position the platform holds but the engine has forgotten is
        # UNMANAGED -- no take-profit, and far worse, no trailing stop.
        # Adopt it so the exit rules apply again.
        #
        # The cooldown above is what makes this safe. Without it,
        # recovery re-adopts a position the stop closed two bars earlier,
        # undoing the exit at the same price.
        if holdings:
            for t in self.tickers:
                if t in self.active_positions or t in self.exited_tickers:
                    continue
                if t in self.exit_cooldown:
                    continue
                if len(self.active_positions) >= self.max_positions:
                    break
                try:
                    held = float(holdings.get(t, 0) or 0)
                except (TypeError, ValueError):
                    held = 0.0
                if held <= 0:
                    continue
                bar = d[-1].get(t)
                if not bar:
                    continue
                cp = bar["close"]
                w = self._observed_weight(t, holdings)
                self.active_positions[t] = {
                    "entry_price": cp,
                    "peak_price": cp,
                    "weight": w if w is not None else self.allocation_size,
                }
                log(f"AMNESIA RECOVERY: adopted untracked {t} at {cp}")

        self.exited_tickers = []
        state_changed = False
        newly_entered = set()

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
                self.exit_cooldown[t] = self.exit_cooldown_bars
                del self.active_positions[t]
                state_changed = True
                continue

            st = self.trailing_stop_for(t)
            if cp <= m["peak_price"] * (1 - st):
                log(f"SWING STOP ({st:.0%}): {t} exit at {cp}.")
                self.exited_tickers.append(t)
                self.exit_cooldown[t] = self.exit_cooldown_bars
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
                log(f"SWING ENTRY (33%): {best} | RVOL: {scores[best]:.2f}")

        # --- 3. submit only on a real entry or exit ----------------
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