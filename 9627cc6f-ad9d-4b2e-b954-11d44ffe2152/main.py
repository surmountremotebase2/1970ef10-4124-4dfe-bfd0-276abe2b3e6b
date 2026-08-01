from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import random


class TradingStrategy(Strategy):
    """
    CONFIG A — QUIET MODE. Tests whether long backtests are actually possible.
    Same logic as the 96/8 config; per-trade logging replaced with periodic
    aggregate reports to minimize log streaming.

    Run on 2021-11-01 to 2023-11-01 (2 years), slippage 0. seed 42.

    If this completes: multi-year backtests are available, and a true
    200-day macro filter is fully evaluable.
    If it fails at an inconsistent point again: retry once before concluding
    anything — prior failures were load-dependent, not deterministic.
    """

    def __init__(self):
        self.tickers = ["TECL", "SOXL", "GDXU", "AGQ", "UCO"]
        self.clusters = {"TECL": "tech", "SOXL": "tech", "AGQ": "silver",
                         "GDXU": "gold", "UCO": "energy"}

        self.max_positions = 3
        self.max_weight_per_position = 0.40
        self.min_cash_buffer = 0.05

        self.take_profit_pct = 0.10
        self.trailing_stop_pct = 0.08
        self.hard_stop_pct = 0.12
        self.max_hold_bars = 96

        self.seed = 42
        self.rng = random.Random(self.seed)
        self.exit_cooldown_bars = 3

        self.active_positions = {}
        self.cooldown = {}

        self.bar_count = 0
        self.report_every = 5000
        self.entry_count = 0
        self.stats = {}
        self._started = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def _close(self, ticker, ohlcv):
        bar = ohlcv[-1]
        if ticker in bar:
            return float(bar[ticker]["close"])
        for row in reversed(ohlcv[-10:]):
            if ticker in row:
                return float(row[ticker]["close"])
        return None

    def _record(self, kind, r):
        if kind not in self.stats:
            self.stats[kind] = [0, 0.0]
        self.stats[kind][0] += 1
        self.stats[kind][1] += r

    def _report(self, ohlcv):
        parts = []
        for k in sorted(self.stats):
            n, tot = self.stats[k]
            parts.append(f"{k} n={n} avg={tot / n * 100:+.2f}%")
        date = "?"
        try:
            date = str(ohlcv[-1][self.tickers[0]].get("date", "?"))[:10]
        except Exception:
            pass
        log(f"[{date}] bar {self.bar_count} | entries={self.entry_count} | " + " | ".join(parts))

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return None

        if not self._started:
            log(f"CONFIG A QUIET | seed={self.seed} | hold=96 | trail=8% | hard=12%")
            self._started = True

        self.bar_count += 1
        holdings = data.get("holdings", {}) or {}
        state_changed = False

        for t in list(self.cooldown.keys()):
            self.cooldown[t] -= 1
            if self.cooldown[t] <= 0:
                del self.cooldown[t]

        for t in self.tickers:
            held = holdings.get(t, 0)
            if held and held > 0.001 and t not in self.active_positions and t not in self.cooldown:
                cp = self._close(t, ohlcv)
                if cp:
                    self.active_positions[t] = {
                        "entry_price": cp, "peak_price": cp, "bars_held": 0,
                        "weight": float(self.max_weight_per_position), "resynced": True}
                    state_changed = True

        for t in list(self.active_positions.keys()):
            cp = self._close(t, ohlcv)
            if cp is None:
                continue

            pos = self.active_positions[t]
            pos["bars_held"] += 1
            if cp > pos["peak_price"]:
                pos["peak_price"] = cp

            suppress_tp = pos.get("resynced") and pos["bars_held"] <= 1

            exit_reason = None
            if not suppress_tp and cp >= pos["entry_price"] * (1 + self.take_profit_pct):
                exit_reason = "TP"
            elif cp <= pos["entry_price"] * (1 - self.hard_stop_pct):
                exit_reason = "HARDSTOP"
            elif cp <= pos["peak_price"] * (1 - self.trailing_stop_pct):
                exit_reason = "TRAIL"
            elif pos["bars_held"] >= self.max_hold_bars:
                exit_reason = "TIMESTOP"

            if exit_reason:
                r = (cp - pos["entry_price"]) / pos["entry_price"]
                self._record(exit_reason, r)
                del self.active_positions[t]
                self.cooldown[t] = self.exit_cooldown_bars
                state_changed = True
            elif pos.get("resynced"):
                pos["resynced"] = False

        while len(self.active_positions) < self.max_positions:
            active_clusters = {self.clusters[t] for t in self.active_positions}
            eligible = [t for t in self.tickers
                        if t not in self.active_positions
                        and t not in self.cooldown
                        and self.clusters[t] not in active_clusters
                        and self._close(t, ohlcv) is not None]
            if not eligible:
                break

            t = self.rng.choice(eligible)
            price = self._close(t, ohlcv)

            used = sum(p["weight"] for p in self.active_positions.values())
            remaining = 1.0 - self.min_cash_buffer - used
            weight = float(min(self.max_weight_per_position, remaining))
            if weight < 0.05:
                break

            self.active_positions[t] = {
                "entry_price": float(price), "peak_price": float(price),
                "bars_held": 0, "weight": weight, "resynced": False}
            self.entry_count += 1
            state_changed = True

        if self.bar_count % self.report_every == 0 and self.stats:
            self._report(ohlcv)

        if state_changed:
            return TargetAllocation({t: float(p["weight"]) for t, p in self.active_positions.items()})

        return None