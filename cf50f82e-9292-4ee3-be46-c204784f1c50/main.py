from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Original 5-Ticker Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
       
        # Dual-Bullet Parameters
        self.allocation_size = 0.50 # 50% max per trade slot
        self.max_positions = 2 # Maximum of 2 concurrent bullets
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
       
        # Internal Memory Tracker
        self.active_positions = {}
        self.exited_tickers = []

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

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
       
        # --- PENDING ORDER SHIELD ---
        # Prevents race conditions by aborting the run if the broker hasn't settled previous trades.
        orders = data.get("orders", [])
        for order in orders:
            if order.get("status") in ["pending", "open", "submitted", "accepted"]:
                log("SHIELD ACTIVE: Unsettled orders detected. Aborting run to prevent race conditions.")
                return None
       
        holdings = data.get("holdings", {})
       
        # --- AMNESIA RECOVERY CIRCUIT Breaker ---
        if holdings:
            for t in self.tickers:
                if holdings.get(t, 0) > 0 and t not in self.active_positions:
                    if t not in self.exited_tickers and len(self.active_positions) < self.max_positions:
                        cp = d[-1][t]["close"] if t in d[-1] else 0
                        self.active_positions[t] = {"entry_price": cp, "peak_price": cp}
                        log(f"AMNESIA RECOVERY: Resynced live position for {t}")

        self.exited_tickers = []
        state_changed = False

        # --- 1. SWING MANAGEMENT (Independent tracking) ---
        for t, metrics in list(self.active_positions.items()):
            current_bar = d[-1].get(t)
            if not current_bar: continue
           
            cp = current_bar["close"]
           
            if cp > metrics["peak_price"]:
                self.active_positions[t]["peak_price"] = cp
           
            # OFFENSIVE EXIT: 10% Target
            if cp >= metrics["entry_price"] * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

        # --- 2. PREDATORY SELECTION ---
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                if t in self.active_positions:
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
                    "peak_price": d[-1][best_ticker]["close"]
                }
                state_changed = True
                log(f"SWING ENTRY (50%): {best_ticker} | RVOL: {scores[best_ticker]:.2f}")

        # --- 3. ALLOCATION EXECUTION (Bulletproof Dynamic Mapping) ---
        if state_changed:
            cash = holdings.get("CASH", 0)
            current_values = {}
            total_portfolio_value = cash
           
            # Calculate the actual real-time dollar value of all open positions
            for t in self.tickers:
                shares = holdings.get(t, 0)
                if shares > 0 and t in d[-1]:
                    asset_value = shares * d[-1][t]["close"]
                    current_values[t] = asset_value
                    total_portfolio_value += asset_value
           
            # The Math Shield: Prevents the ZeroDivisionError if backtester hides data
            if total_portfolio_value <= 0:
                new_allocation = {t: self.allocation_size for t in self.active_positions}
                return TargetAllocation(new_allocation)
           
            new_allocation = {}
            for t in self.active_positions:
                if t in current_values and current_values[t] > 0:
                    # Freeze existing positions by matching their exact current percentage value
                    new_allocation[t] = current_values[t] / total_portfolio_value
                else:
                    # Deploy available cash cleanly into the new position without touching the winner
                    target_value = min(cash, total_portfolio_value * self.allocation_size)
                    new_allocation[t] = target_value / total_portfolio_value
                   
            return TargetAllocation(new_allocation)

        return None