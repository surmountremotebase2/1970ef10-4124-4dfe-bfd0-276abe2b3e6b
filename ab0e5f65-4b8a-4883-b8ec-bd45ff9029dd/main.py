from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import random


class TradingStrategy(Strategy):
    """
    NULL TEST — random entry, identical exit framework to v5.
    5-YEAR RUN: set backtest window to 2021-07-30 through 2026-07-30.
    Run three times, changing only self.seed: 42, then 7, then 123.
    """

    def __init__(self):
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]

        self.clusters = {
            "TECL": "tech",
            "SOXL": "tech",
            "GDXU": "metals",
            "AGQ": "metals",
            "UCO": "energy",
        }

        self.max_positions = 3
        self.max_weight_per_position = 0.40
        self.min_cash_buffer = 0.05

        self.take_profit_pct = 0.10
        self.trailing_stop_pct = 0.08
        self.hard_stop_pct = 0.12
        self.max_hold_bars = 96

        # --- CHANGE THIS ONE LINE BETWEEN RUNS: 42, then 7, then 123 ---
        self.seed = 42

        self.entry_probability = 1.0
        self.rng = random.Random(self.seed)

        self.exit_cooldown_bars = 3

        self.active_positions = {}
        self.cooldown = {}
        self._logged_diagnostics = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def _log_diagnostics_once(self, data):
        if self._logged_diagnostics:
            return
        log(f"NULL TEST running | seed={self.seed} | entry_prob={self.entry_probability}")
        log(f"DIAGNOSTIC: raw holdings = {data.get('holdings')}")
        self._logged_diagnostics = True

    def _latest_close(self, ticker, ohlcv):
        for row in reversed(ohlcv):
            if ticker in row:
                return float(row[ticker]["close"])
        return None

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return None

        self._log_diagnostics_once(data)
        holdings = data.get("holdings", {}) or {}
        state_changed = False

        # --- tick down exit cooldowns ---
        for t in list(self.cooldown.keys()):
            self.cooldown[t] -= 1
            if self.cooldown[t] <= 0:
                del self.cooldown[t]

        # --- resync orphaned positions (cooldown-guarded) ---
        for t in self.tickers:
            held = holdings.get(t, 0)
            if held and held > 0.001 and t not in self.active_positions and t not in self.cooldown:
                cp = self._latest_close(t, ohlcv)
                if cp:
                    log(f"RESYNC: {t} held but untracked — proxy entry {cp}.")
                    self.active_positions[t] = {
                        "entry_price": cp,
                        "peak_price": cp,
                        "bars_held": 0,
                        "weight": float(self.max_weight_per_position),
                        "resynced": True,
                    }
                    state_changed = True

        # --- exits ---
        for t in list(self.active_positions.keys()):
            cp = self._latest_close(t, ohlcv)
            if cp is None:
                continue

            pos = self.active_positions[t]
            pos["bars_held"] += 1
            if cp > pos["peak_price"]:
                pos["peak_price"] = cp

            suppress_tp = pos.get("resynced") and pos["bars_held"] <= 1

            exit_reason = None
            if not suppress_tp and cp >= pos["entry_price"] * (1 + self.take_profit_pct):
                exit_reason = "TAKE PROFIT"
            elif cp <= pos["entry_price"] * (1 - self.hard_stop_pct):
                exit_reason = "HARD STOP"
            elif cp <= pos["peak_price"] * (1 - self.trailing_stop_pct):
                exit_reason = "TRAILING STOP"
            elif pos["bars_held"] >= self.max_hold_bars:
                exit_reason = "TIME STOP (stalled trade)"

            if exit_reason:
                log(f"{exit_reason}: {t} exit at {cp} | entry {pos['entry_price']} | held {pos['bars_held']} bars")
                del self.active_positions[t]
                self.cooldown[t] = self.exit_cooldown_bars
                state_changed = True
            elif pos.get("resynced"):
                pos["resynced"] = False

        # --- RANDOM ENTRY ---
        while len(self.active_positions) < self.max_positions:
            active_clusters = {self.clusters[t] for t in self.active_positions}
            eligible = [
                t for t in self.tickers
                if t not in self.active_positions
                and t not in self.cooldown
                and self.clusters[t] not in active_clusters
                and self._latest_close(t, ohlcv) is not None
            ]
            if not eligible:
                break
            if self.rng.random() > self.entry_probability:
                break

            t = self.rng.choice(eligible)
            price = self._latest_close(t, ohlcv)

            used_weight = sum(p["weight"] for p in self.active_positions.values())
            remaining_capacity = 1.0 - self.min_cash_buffer - used_weight
            weight = float(min(self.max_weight_per_position, remaining_capacity))
            if weight < 0.05:
                break

            self.active_positions[t] = {
                "entry_price": float(price),
                "peak_price": float(price),
                "bars_held": 0,
                "weight": weight,
                "resynced": False,
            }
            state_changed = True
            log(f"ENTRY: {t} | weight {weight:.2%} | RVOL 0.00 | cluster {self.clusters[t]}")

        # --- only send an allocation when something actually changed ---
        if state_changed:
            allocation = {t: float(p["weight"]) for t, p in self.active_positions.items()}
            return TargetAllocation(allocation)

        return None