from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # 4-Ticker Macro Roster. UCO REMOVED 2026-08-27.
        # Measured across 219 real round trips over 3 years, priced
        # against SIP data: UCO was the only negative contributor
        # (-9.5%), had the worst win rate (32%), reached the +25%
        # target least often (10% vs 18-24%), and held the longest
        # (14.8 days vs 4-13). It consumed 31% of ALL position-time
        # -- more than any other ticker -- at -0.016% per slot-day.
        # With only two slots that is not neutral, it is blocking.
        # It is also the only futures-based product here, so it pays
        # contango roll decay the equity/metal funds do not.
        self.tickers = ["TECL", "GDXU", "SOXL", "AGQ"]

        # Dual-Bullet Parameters
        self.allocation_size = 0.50 # 50% per trade
        self.max_positions = 2      # Maximum of 2 concurrent bullets
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.10 # CHANGED 2026-08-27: was 0.08
        self.take_profit_pct = 0.25   # held at 0.25 -- the stop does ~88% of exits

        # THEME GUARD -- added 2026-08-27.
        # Measured on the real 4-ticker log: the -53.3% drawdown ran
        # 2026-01-28 to 2026-03-27. Same-theme periods were 23% of that
        # window and produced -50.5% of it. Mixed-theme periods were 40%
        # of it and were POSITIVE (+7.6%). Across the full year, mixed
        # returned +75.0% at 46.1% vol while same-theme returned +14.6%
        # at 59.6% vol -- more return AND less risk, on both axes.
        #
        # Holding two funds from one theme is one bet at 100%, not two at
        # 50%. This is exactly what UCO was doing accidentally in the
        # 5-ticker roster: occupying a slot so the book could not double
        # up. It cost -9.5% to provide that. The guard provides it free.
        self.themes = {"SOXL": "TECH", "TECL": "TECH",
                       "GDXU": "METAL", "AGQ": "METAL", "UCO": "ENERGY"}
        # size for a second position in a theme already held:
        #   0.00 = refuse it (strictest)
        #   0.25 = take it at half size (participate, halve the doubling)
        #   0.50 = no guard, original behaviour
        self.same_theme_size = 0.25

        self.active_positions = {}
        self.exited_tickers = [] # Circuit breaker to handle backtester settlement lag

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 200: return 0
        df = pd.DataFrame(history)

        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]

        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0

        sma_macro = df['close'].tail(200).mean()

        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def _observed_weight(self, ticker, holdings):
        """The position's ACTUAL current weight, as reported by the platform.

        Returns None if unavailable or not expressed as a fraction, in which
        case the caller falls back to the last weight we tracked ourselves.
        """
        if not holdings:
            return None
        raw = holdings.get(ticker, None)
        if raw is None:
            return None
        try:
            w = float(raw)
        except (TypeError, ValueError):
            return None
        # Treat as a portfolio fraction only if it looks like one.
        if 0.0 < w <= 1.0:
            return w
        return None

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None

        holdings = data.get("holdings", {})

        # --- AMNESIA RECOVERY CIRCUIT BREAKER -- DISABLED FOR THIS TEST ---
        # Fired 41 times over the 3-year window. Each firing re-adopted a
        # position at the CURRENT price with a fresh entry price and a fresh
        # peak, which restarts the take-profit and trailing-stop clocks and
        # effectively gives a winner a second life. That is a side effect of
        # working around settlement lag, not a trading decision.
        #
        # Disabled here to measure how much of the return it was producing.
        # If results fall sharply, the mechanism was doing the work.
        # To restore, simply uncomment the block below.
        #
        # if holdings:
        #     for t in self.tickers:
        #         if holdings.get(t, 0) > 0 and t not in self.active_positions:
        #             if t not in self.exited_tickers and len(self.active_positions) < self.max_positions:
        #                 cp = d[-1][t]["close"] if t in d[-1] else 0
        #                 recovered_w = self._observed_weight(t, holdings)
        #                 self.active_positions[t] = {
        #                     "entry_price": cp,
        #                     "peak_price": cp,
        #                     "weight": recovered_w if recovered_w is not None else self.allocation_size,
        #                 }
        #                 log(f"AMNESIA RECOVERY: Resynced live position for {t}")

        # Clear the lag circuit breaker at the start of a new bar
        self.exited_tickers = []
        state_changed = False
        newly_entered = set()

        # --- REFRESH TRACKED WEIGHTS (no trading, bookkeeping only) ---
        # Record what each position is ACTUALLY worth right now so that the
        # allocation we submit later matches reality and triggers no trade.
        for t in self.active_positions:
            observed = self._observed_weight(t, holdings)
            if observed is not None:
                self.active_positions[t]["weight"] = observed

        # --- 1. SWING MANAGEMENT (Manage held positions independently) ---
        for t, metrics in list(self.active_positions.items()):
            current_bar = d[-1].get(t)
            if not current_bar: continue

            cp = current_bar["close"]

            if cp > metrics["peak_price"]:
                self.active_positions[t]["peak_price"] = cp

            # OFFENSIVE EXIT: 25% Target
            if cp >= metrics["entry_price"] * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

            # DEFENSIVE EXIT: 10% Trailing Stop
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

        # --- 2. PREDATORY SELECTION (Unmuzzled) ---
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                # The Sieve: Prevent buying a ticker we already hold
                if t in self.active_positions:
                    continue

                hist = [bar[t] for bar in d if t in bar]
                if len(hist) > 0:
                    score = self.get_conviction_score(hist)
                    if score > 0:
                        scores[t] = score

            if scores:
                # THEME GUARD: prefer a ticker whose theme is not already
                # held. Fall back to a same-theme name only if
                # same_theme_size allows it, and then at reduced size.
                held_themes = {self.themes.get(t) for t in self.active_positions}
                fresh = {t: s for t, s in scores.items()
                         if self.themes.get(t) not in held_themes}

                if fresh:
                    best_ticker = max(fresh, key=fresh.get)
                    entry_size = self.allocation_size
                    tag = "SWING ENTRY"
                elif self.same_theme_size > 0:
                    best_ticker = max(scores, key=scores.get)
                    entry_size = self.same_theme_size
                    tag = "SWING ENTRY (same theme, reduced)"
                else:
                    best_ticker = None

                if best_ticker:
                    self.active_positions[best_ticker] = {
                        "entry_price": d[-1][best_ticker]["close"],
                        "peak_price": d[-1][best_ticker]["close"],
                        "weight": entry_size,
                    }
                    newly_entered.add(best_ticker)
                    self.entry_sizes = getattr(self, "entry_sizes", {})
                    self.entry_sizes[best_ticker] = entry_size
                    state_changed = True

                    log(f"{tag} ({entry_size:.0%}): {best_ticker} "
                        f"| RVOL: {scores[best_ticker]:.2f}")

        # --- 3. ALLOCATION EXECUTION (no forced rebalancing) ---
        # Only a NEW position is assigned allocation_size. Existing positions
        # are submitted at the weight they have actually drifted to, so the
        # platform sees no difference from the current holding and places no
        # trade. Previously every position was reset to 0.50 on any state
        # change, which sold down winners and topped up losers.
        if state_changed:
            new_allocation = {}
            for t, metrics in self.active_positions.items():
                if t in newly_entered:
                    new_allocation[t] = getattr(self, "entry_sizes", {}).get(
                        t, self.allocation_size)
                else:
                    new_allocation[t] = metrics.get("weight", self.allocation_size)

            total = sum(new_allocation.values())
            if total > 1.0:
                # Never submit more than 100% of capital. Scale proportionally
                # rather than truncating any single position.
                new_allocation = {t: w / total for t, w in new_allocation.items()}

            log("ALLOC: " + ", ".join(f"{t} {w:.1%}" for t, w in new_allocation.items()))
            return TargetAllocation(new_allocation)

        return None