from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import random


class TradingStrategy(Strategy):
    """
    SIGNAL v1 — PULLBACK-IN-UPTREND entry.
    Inverse of the original RVOL-spike logic: buy the dip inside an intact
    trend, on CALM volume, rather than buying the volume explosion.

    Entry requires ALL of:
      1. price > 50-bar SMA (trend intact)
      2. pulled back 1.5%-6% from the 24-bar high (not buying the top)
      3. RVOL <= 1.5 (calm, not a climax bar)
    Ranked by pullback depth — deepest qualifying dip wins the slot.

    Exits unchanged and validated: 10% TP, 8% trailing, 12% hard, 96-bar time.
    Clusters unchanged: tech (TECL/SOXL exclusive), silver, gold, energy.

    Seed now only breaks ties. If the signal is real, seeds should converge.
    Random baseline to beat (same window): 10.10 / 33.92 / 66.56 / 104.77 / 158.47, mean ~75%.
    Run on 2022-07-31 to 2023-07-31, slippage 0.
    """

    def __init__(self):
        self.tickers = ["TECL", "SOXL", "GDXU", "AGQ", "UCO"]

        self.clusters = {
            "TECL": "tech",
            "SOXL": "tech",
            "AGQ": "silver",
            "GDXU": "gold",
            "UCO": "energy",
        }

        self.max_positions = 3
        self.max_weight_per_position = 0.40
        self.min_cash_buffer = 0.05

        self.take_profit_pct = 0.10
        self.trailing_stop_pct = 0.08
        self.hard_stop_pct = 0.12
        self.max_hold_bars = 96

        # --- SIGNAL PARAMETERS ---
        self.trend_lookback = 50
        self.high_lookback = 24
        self.min_pullback = 0.015
        self.max_pullback = 0.06
        self.rvol_ceiling = 1.5
        self.vol_lookback = 20

        self.seed = 42
        self.rng = random.Random(self.seed)
        self.exit_cooldown_bars = 3

        self.active_positions = {}
        self.cooldown = {}
        self.bar_count = 0
        self.qualify_count = 0
        self._logged_diagnostics = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def _log_diagnostics_once(self):
        if self._logged_diagnostics:
            return
        log(f"SIGNAL v1 PULLBACK | seed={self.seed} | trend>{self.trend_lookback}sma "
            f"| pullback {self.min_pullback:.1%}-{self.max_pullback:.1%} "
            f"| rvol<={self.rvol_ceiling}")
        self._logged_diagnostics = True

    def _latest_close(self, ticker, ohlcv):
        for row in reversed(ohlcv):
            if ticker in row:
                return float(row[ticker]["close"])
        return None

    def _pullback_score(self, ticker, ohlcv):
        """Returns pullback depth if the setup qualifies, else None."""
        rows = [bar[ticker] for bar in ohlcv if ticker in bar]
        if len(rows) < self.trend_lookback + 5:
            return None

        df = pd.DataFrame(rows)
        current = float(df["close"].iloc[-1])

        sma_trend = float(df["close"].tail(self.trend_lookback).mean())
        if current <= sma_trend:
            return None

        if "high" in df.columns:
            recent_high = float(df["high"].tail(self.high_lookback).max())
        else:
            recent_high = float(df["close"].tail(self.high_lookback).max())

        if recent_high <= 0:
            return None

        pullback = (recent_high - current) / recent_high
        if pullback < self.min_pullback or pullback > self.max_pullback:
            return None

        avg_vol = float(df["volume"].tail(self.vol_lookback).mean())
        if avg_vol <= 0:
            return None
        rvol = float(df["volume"].iloc[-1]) / avg_vol
        if rvol > self.rvol_ceiling:
            return None

        return float(pullback)

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return None

        self._log_diagnostics_once()
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

        # --- SIGNAL-DRIVEN ENTRY ---
        while len(self.active_positions) < self.max_positions:
            active_clusters = {self.clusters[t] for t in self.active_positions}

            candidates = []
            for t in self.tickers:
                if t in self.active_positions or t in self.cooldown:
                    continue
                if self.clusters[t] in active_clusters:
                    continue
                score = self._pullback_score(t, ohlcv)
                if score is not None:
                    candidates.append((t, score))

            if not candidates:
                break

            best_score = max(c[1] for c in candidates)
            tied = [c[0] for c in candidates if abs(c[1] - best_score) < 1e-9]
            t = tied[0] if len(tied) == 1 else self.rng.choice(tied)

            price = self._latest_close(t, ohlcv)
            if price is None:
                break

            used = sum(p["weight"] for p in self.active_positions.values())
            remaining = 1.0 - self.min_cash_buffer - used
            weight = float(min(self.max_weight_per_position, remaining))
            if weight < 0.05:
                break

            self.active_positions[t] = {
                "entry_price": float(price),
                "peak_price": float(price),
                "bars_held": 0,
                "weight": weight,
                "resynced": False,
            }
            self.qualify_count += 1
            state_changed = True
            log(f"ENTRY: {t} | weight {weight:.2%} | pullback {best_score:.2%} | cluster {self.clusters[t]}")

        if self.bar_count % 4000 == 0:
            log(f"[bar {self.bar_count}] signal entries so far: {self.qualify_count} "
                f"| open positions: {len(self.active_positions)}")

        if state_changed:
            return TargetAllocation({t: float(p["weight"]) for t, p in self.active_positions.items()})

        return None