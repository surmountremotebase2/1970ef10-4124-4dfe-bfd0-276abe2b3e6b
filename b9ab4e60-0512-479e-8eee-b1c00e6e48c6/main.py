from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Final 2026 Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
        
        # Core Engine Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08 # 8% Trailing Stop
        self.take_profit_pct = 0.05 # <-- Adjusted to 5% Target
        self.max_allocation = 1.0 # 100% Allocation for Post-PDT
        
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        # Memory buffer check for platform stability
        if len(history) < 78: return 0
        df = pd.DataFrame(history)
        
        # VWAP calculation (12-period)
        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        # RVOL calculation (Current volume vs 20-period average)
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        # Macro Trend Check
        sma_macro = df['close'].mean()
        
        # Trigger Conditions: Above VWAP, Above SMA, and High Volume
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # --- 1. EXIT MANAGEMENT (5% Target & 8% Stop) ---
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            
            cp = current_bar["close"]
            
            # Update peak price for the trailing stop
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp
            
            # OFFENSIVE EXIT: Lock in 5% gain
            if self.entry_price and cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {self.active_ticker} exit at {cp}. Secured 5% gain.")
                self._reset_engine()
                return TargetAllocation({})

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"STOP LOSS: {self.active_ticker} exit at {cp}. Peak was {self.peak_price}.")
                self._reset_engine()
                return TargetAllocation({})
            
            return None

        # --- 2. PREDATORY SELECTION (100% Entry) ---
        scores = {}
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            score = self.get_conviction_score(hist)
            if score > 0:
                scores[t] = score
        
        if scores:
            best_ticker = max(scores, key=scores.get)
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            self.entry_price = d[-1][best_ticker]["close"]
            
            log(f"ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Target: {self.entry_price * 1.05:.2f}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None

    def _reset_engine(self):
        """Reset all tracking variables after an exit."""
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None