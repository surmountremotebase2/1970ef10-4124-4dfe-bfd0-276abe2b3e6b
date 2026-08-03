from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Original 5-Ticker Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]

        # Dual-Bullet Parameters
        self.allocation_size = 0.50
        self.max_positions = 2
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10

        # Internal Memory Tracker
        # Each position carries "weight" -- its last known share of the
        # portfolio. This is what stops the forced rebalancing.
        self.active_positions = {}

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 200: return 0
        df = pd.DataFrame(history)

        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]

        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0

        sma_macro = df['close'].tail(200).mean()

        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def _observed_weight(self, ticker, holdings):
        """The position's CURRENT portfolio weight, if the platform reports
        it as a fraction. Returns None otherwise, so callers fall back to
        the weight we tracked ourselves."""
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
        if not d: return None

        # --- PHASE 1: THE DATA SCRUBBER ---
        # Force uppercase so API casing cannot cause duplicate buys.
        raw_holdings = data.get("holdings", {})
        holdings = {str(k).upper(): v for k, v in raw_holdings.items()}

        state_changed = False
        newly_entered = set()

        # --- PHASE 2: RECONCILIATION ---
        # If the broker reports a position the strategy has no record of, do
        # NOT adopt it -- close it. An untracked leveraged position has no
        # stop and no target protecting it. Because the allocation dict is
        # built only from active_positions, anything untracked simply will
        # not appear in it, and the platform closes it out.
        for t in self.tickers:
            if holdings.get(t, 0) > 0.01 and t not in self.active_positions:
                log(f"RECONCILE: {t} held but untracked -- closing.")
                state_changed = True

        # --- REFRESH TRACKED WEIGHTS (bookkeeping only, places no trades) ---
        for t in self.active_positions:
            observed = self._observed_weight(t, holdings)
            if observed is not None:
                self.active_positions[t]["weight"] = observed

        # --- PHASE 3: SWING MANAGEMENT ---
        for t, metrics in list(self.active_positions.items()):
            current_bar = d[-1].get(t)
            if not current_bar: continue

            cp = current_bar["close"]

            if cp > metrics["peak_price"]:
                self.active_positions[t]["peak_price"] = cp

            # OFFENSIVE EXIT: 10% Target
            if cp >= metrics["entry_price"] * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {t} exit at {cp}.")
                del self.active_positions[t]
                state_changed = True
                continue

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                del self.active_positions[t]
                state_changed = True
                continue

        # --- PHASE 4: PREDATORY SELECTION ---
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                # The Sieve: skip if tracked in memory OR physically held
                if t in self.active_positions or holdings.get(t, 0) > 0.01:
                    continue

                hist = [bar[t] for bar in d if t in bar]
                if len(hist) > 0:
                    score = self.get_conviction_score(hist)
                    if score > 0:
                        scores[t] = score

            if scores:
                best_ticker = max(scores, key=scores.get)

                self.active_positions[best_ticker] = {
                    "entry_price": d[-1][best_ticker]["close"],
                    "peak_price": d[-1][best_ticker]["close"],
                    "weight": self.allocation_size,
                }
                newly_entered.add(best_ticker)
                state_changed = True
                log(f"SWING ENTRY (50%): {best_ticker} | RVOL: {scores[best_ticker]:.2f}")

        # --- PHASE 5: ENVIRONMENT-ADAPTIVE ALLOCATION ---
        if state_changed:
            # LIVE ENGINE SWITCH: real account, 'CASH' key present
            if "CASH" in holdings:
                cash = holdings.get("CASH", 0)
                current_values = {}
                total_portfolio_value = cash

                for t in self.tickers:
                    shares = holdings.get(t, 0)
                    if shares > 0.01 and t in d[-1]:
                        asset_value = shares * d[-1][t]["close"]
                        current_values[t] = asset_value
                        total_portfolio_value += asset_value

                if total_portfolio_value > 0:
                    new_allocation = {}
                    for t in self.active_positions:
                        if holdings.get(t, 0) > 0.01 and t in current_values:
                            # Lock an existing position to its exact current
                            # weight so the platform places no trade on it.
                            new_allocation[t] = current_values[t] / total_portfolio_value
                        else:
                            target_value = min(cash, total_portfolio_value * self.allocation_size)
                            new_allocation[t] = target_value / total_portfolio_value
                    log("ALLOC: " + ", ".join(f"{t} {w:.1%}" for t, w in new_allocation.items()))
                    return TargetAllocation(new_allocation)

            # SANDBOX PATH -- the one backtests use.
            # This used to reset every position to 50%, which sold down
            # winners and topped up losers on every state change. Now only a
            # NEW position gets allocation_size; anything already held is
            # submitted at the weight it has actually drifted to, so the
            # platform sees no change and places no trade.
            new_allocation = {}
            for t, metrics in self.active_positions.items():
                if t in newly_entered:
                    new_allocation[t] = self.allocation_size
                else:
                    new_allocation[t] = metrics.get("weight", self.allocation_size)

            total = sum(new_allocation.values())
            if total > 1.0:
                # Never submit more than 100% of capital.
                new_allocation = {t: w / total for t, w in new_allocation.items()}

            log("ALLOC: " + ", ".join(f"{t} {w:.1%}" for t, w in new_allocation.items()))
            return TargetAllocation(new_allocation)

        return None