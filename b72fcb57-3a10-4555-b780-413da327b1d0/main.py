from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Original 5-Ticker Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
        
        # Dual-Bullet Parameters
        self.allocation_size = 0.50 # 50% per trade
        self.max_positions = 2 # Maximum of 2 concurrent bullets
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        
        # Internal Memory Tracker
        # Format: {"TICKER": {"entry_price": X, "peak_price": Y}}
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

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        holdings = data.get("holdings", {})
        
        # --- AMNESIA RECOVERY CIRCUIT BREAKER ---
        if holdings:
            for t in self.tickers:
                if holdings.get(t, 0) > 0 and t not in self.active_positions:
                    if t not in self.exited_tickers and len(self.active_positions) < self.max_positions:
                        # Re-sync a missing live position
                        cp = d[-1][t]["close"] if t in d[-1] else 0
                        self.active_positions[t] = {"entry_price": cp, "peak_price": cp}
                        log(f"AMNESIA RECOVERY: Resynced live position for {t}")

        # Clear the lag circuit breaker at the start of a new bar
        self.exited_tickers = []
        state_changed = False

        # --- 1. SWING MANAGEMENT ---
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

        # --- 2. PREDATORY SELECTION (10:00 AM Opening Range Block) ---
        if len(self.active_positions) < self.max_positions:
            
            current_time_str = d[-1].get("date") or d[-1].get("time")
            if not current_time_str:
                for t in self.tickers:
                    if t in d[-1] and isinstance(d[-1][t], dict):
                        current_time_str = d[-1][t].get("date") or d[-1][t].get("time")
                        if current_time_str:
                            break
                            
            is_safe_trading_window = False 
            
            if current_time_str:
                try:
                    bar_time = pd.to_datetime(current_time_str)
                    # Core market hours open at 9:30 AM ET. We block buying until 10:00 AM ET.
                    if (10 <= bar_time.hour < 16):
                        is_safe_trading_window = True
                except Exception as e:
                    log(f"Time parsing error: {e}")

            if is_safe_trading_window:
                scores = {}
                for t in self.tickers:
                    # Sieve: Prevent buying a ticker we already hold
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
                    
                    log(f"SWING ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f}")

        # --- 3. ALLOCATION EXECUTION (Backtest-Safe Dynamic Freeze) ---
        if state_changed or len(self.active_positions) > 0:
            new_allocation = {}
            
            # 1. Calculate total portfolio value safely
            total_portfolio_value = holdings.get("CASH", 0)
            for t in self.tickers:
                shares = holdings.get(t, 0)
                if shares > 0 and t in d[-1]:
                    total_portfolio_value += shares * d[-1][t]["close"]
            
            # 2. Prevent zero-division on the first backtest bar
            if total_portfolio_value <= 0:
                for t in self.active_positions:
                    new_allocation[t] = self.allocation_size
                return TargetAllocation(new_allocation)

            # 3. Freeze existing positions and size new entries
            for t in self.active_positions:
                shares = holdings.get(t, 0)
                if shares > 0 and t in d[-1]:
                    # Asset is already held: lock its target to its exact current percentage
                    drifted_weight = (shares * d[-1][t]["close"]) / total_portfolio_value
                    new_allocation[t] = drifted_weight
                else:
                    # Brand new entry: deploy available cash, capped at 50%
                    cash_ratio = holdings.get("CASH", 0) / total_portfolio_value
                    new_allocation[t] = min(self.allocation_size, cash_ratio)
            
            return TargetAllocation(new_allocation)
            
        return None