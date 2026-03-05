from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Final 2026 Macro Roster (All 3x Leveraged Equities)
        self.tickers = ["TECL", "GDXU", "DFEN", "SOXL", "UPRO"]
        
        # Core Engine Parameters
        self.vwap_len = 12 
        self.rvol_threshold = 1.8 # Requires 80% volume spike to trigger entry
        self.trailing_stop_pct = 0.08 # 8% Swing buffer to survive morning gaps
        self.max_allocation = 0.50 # 50% Tranche to maintain T+1 cash settlement compliance
        
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        """Evaluates trend and volume to find the most aggressive institutional buying."""
        if len(history) < 20: return 0
        df = pd.DataFrame(history)
        
        # VWAP calculation (12-period)
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        # RVOL calculation (Current volume vs 20-period average)
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        # Macro Trend Check (Price must be above 1-day/78-bar SMA)
        sma_day = df['close'].tail(78).mean()
        
        if current_price > vwap and current_price > sma_day and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # --- 1. SWING MANAGEMENT (8% Trailing Stop) ---
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            
            cp = current_bar["close"]
            
            # Continuously update the peak price to drag the stop-loss upward
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp
            
            # Execute 8% hard stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {self.active_ticker} exit at {cp}. Peak was {self.peak_price}.")
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                return TargetAllocation({})
            
            # Hold position overnight
            return None

        # --- 2. PREDATORY SELECTION (New Entries) ---
        scores = {}
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            score = self.get_conviction_score(hist)
            if score > 0:
                scores[t] = score
        
        if scores:
            # Deploy capital into the single asset with the highest volume conviction
            best_ticker = max(scores, key=scores.get)
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            
            log(f"SWING ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None