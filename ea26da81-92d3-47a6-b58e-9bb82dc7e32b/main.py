from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np


class TradingStrategy(Strategy):

    def __init__(self):
        self.tickers = ["TECL", "SOXL", "AGQ", "UCO", "GDXU"]

        self.allocation_size = 0.33   # INITIAL size only -- never re-imposed
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
        self.take_profit_pct = 0.25     # fallback
        self.trailing_stop_pct = 0.12   # fallback

        # virtual book: notional shares + cash, so weights come from
        # PRICES and nothing depends on the platform reporting holdings
        self.active_positions = {}
        self.cash = 1.0
        self.exited_tickers = []

        # resubmit only when a weight actually moves more than this.
        # Target is never more than 0.25% stale, so the most the platform
        # can trim is 0.25% -- against the 2.60% seen live.
        self.resubmit_tol = 0.0025
        self.last_alloc = None

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

    def _price(self, bar, ticker, fallback):
        if ticker in bar and bar[ticker] and bar[ticker].get("close"):
            return bar[ticker]["close"]
        return fallback

    def _book_value(self, bar):
        v = self.cash
        for t, m in self.active_positions.items():
            v += m["shares"] * self._price(bar, t, m["last_price"])
        return v if v > 0 else 1.0

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

    def run(self, data):
        d = data.get("ohlcv")
        if not d:
            return None
        bar = d[-1]
        holdings = data.get("holdings", {}) or {}
        self.exited_tickers = []
        newly_entered = set()

        # --- AMNESIA RECOVERY --------------------------------------
        # A position the platform holds but the engine has forgotten is
        # unmanaged: no take-profit and, far worse, NO TRAILING STOP.
        for t in self.tickers:
            if t in self.active_positions:
                continue
            try:
                held = float(holdings.get(t, 0) or 0)
            except (TypeError, ValueError):
                held = 0.0
            if held <= 0 or len(self.active_positions) >= self.max_positions:
                continue
            cp = self._price(bar, t, 0)
            if not cp:
                continue
            total = self._book_value(bar)
            value = min(held, 1.0) * total
            self.active_positions[t] = {
                "entry_price": cp,
                "peak_price": cp,
                "shares": value / cp,
                "last_price": cp,
            }
            self.cash = max(self.cash - value, 0.0)
            newly_entered.add(t)
            log(f"AMNESIA RECOVERY: adopted untracked {t} at {cp}")

        for t, m in self.active_positions.items():
            m["last_price"] = self._price(bar, t, m["last_price"])

        # --- 1. manage what is held --------------------------------
        for t, m in list(self.active_positions.items()):
            cp = self._price(bar, t, None)
            if cp is None:
                continue

            if cp > m["peak_price"]:
                m["peak_price"] = cp

            tp = self.take_profit_for(t)
            st = self.trailing_stop_for(t)
            hit_tp = cp >= m["entry_price"] * (1 + tp)
            hit_st = cp <= m["peak_price"] * (1 - st)

            if hit_tp or hit_st:
                log(f"{'TAKE PROFIT' if hit_tp else 'SWING STOP'} "
                    f"({tp if hit_tp else st:.0%}): {t} exit at {cp}.")
                self.cash += m["shares"] * cp
                self.exited_tickers.append(t)
                del self.active_positions[t]

        # --- 2. pick at most one new position ----------------------
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                if t in self.active_positions or t in self.exited_tickers:
                    continue
                hist = [b[t] for b in d if t in b]
                if hist:
                    sc = self.get_conviction_score(hist)
                    if sc > 0:
                        scores[t] = sc

            if scores:
                best = max(scores, key=scores.get)
                cp = self._price(bar, best, None)
                if cp:
                    total = self._book_value(bar)
                    value = min(self.allocation_size * total, self.cash)
                    if value > 0:
                        self.active_positions[best] = {
                            "entry_price": cp,
                            "peak_price": cp,
                            "shares": value / cp,
                            "last_price": cp,
                        }
                        self.cash -= value
                        newly_entered.add(best)
                        log(f"SWING ENTRY ({self.allocation_size:.0%}): "
                            f"{best} | RVOL: {scores[best]:.2f}")

        # --- 3. submit the REAL weights, THROTTLED -----------------
        total = self._book_value(bar)
        alloc = {t: 0.0 for t in self.tickers}
        for t, m in self.active_positions.items():
            alloc[t] = max(min(m["shares"] * m["last_price"] / total, 1.0), 0.0)

        tot = sum(alloc.values())
        if tot > 1.0:
            alloc = {t: w / tot for t, w in alloc.items()}

        changed = bool(self.exited_tickers) or bool(newly_entered)
        if self.last_alloc is None:
            drift = 1.0
        else:
            drift = max(abs(alloc[t] - self.last_alloc.get(t, 0.0))
                        for t in self.tickers)

        # nothing opened or closed and the target is still accurate --
        # stay silent so the platform keeps what it already has
        if not changed and drift < self.resubmit_tol:
            return None

        self.last_alloc = dict(alloc)

        # log only on a real entry or exit; drift refreshes stay silent
        if changed:
            held = ", ".join(f"{t} {w:.1%}" for t, w in alloc.items() if w > 0)
            log("ALLOC: " + (held if held else "FLAT (all zero)"))

        return TargetAllocation(alloc)