from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Final 2026 Macro Roster (UPRO removed)
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
       
        # Core Engine Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        self.max_allocation = 1.00 # Updated to 100% full allocation model 
       
        # Memory variables for tracking trailing stops (no longer the sole gatekeeper)
        self.peak_price = None
        self.entry_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        # Lowered to 78 bars to ensure we don't exceed the platform's memory buffer
        if len(history) < 78: return 0
        df = pd.DataFrame(history)
       
        # VWAP calculation (12-period)
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
       
        # RVOL calculation (Current volume vs 20-period average)
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
       
        # Macro Trend Check (Calculates the mean over the max available rolling buffer)
        sma_macro = df['close'].mean()
       
        # Asset must be above VWAP and the rolling SMA to filter out dead-cat bounces
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        holdings = data.get("holdings", {})
        if not d: return None
        
        # --- THE FIX: Scan live broker data to see if we physically own an asset ---
        active_ticker = None
        for t in self.tickers:
            # If the broker ledger says we have allocation in this ticker, it is active
            if holdings.get(t, 0) > 0:
                active_ticker = t
                break
       
        # --- 1. SWING MANAGEMENT (10% Take-Profit & 8% Trailing Stop) ---
        if active_ticker:
            current_bar = d[-1].get(active_ticker)
            if not current_bar: return None
           
            cp = current_bar["close"]
           
            # AMNESIA RECOVERY: If server restarted, reset the peak/entry to current price to restore the safety net
            if self.peak_price is None or self.entry_price is None:
                self.peak_price = cp
                self.entry_price = cp
                log(f"RECOVERY OVERRIDE: Restored tracking for {active_ticker} at {cp}")
           
            # Continuously update the peak price to drag the stop-loss upward
            if cp > self.peak_price:
                self.peak_price = cp
           
            # OFFENSIVE EXIT: Lock in 10% hard gain
            if cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {active_ticker} exit at {cp}. Secured 10% gain.")
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {active_ticker} exit at {cp}. Peak was {self.peak_price}.")
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})
           
            # Hold position overnight
            return None

        # --- 2. PREDATORY SELECTION (New Entries) ---
        scores = {}
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            if len(hist) > 0:
                score = self.get_conviction_score(hist)
                if score > 0:
                    scores[t] = score
       
        if scores:
            # Deploy capital into the single asset with the highest volume conviction
            best_ticker = max(scores, key=scores.get)
           
            self.peak_price = d[-1][best_ticker]["close"]
            self.entry_price = d[-1][best_ticker]["close"]
           
            log(f"SWING ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Entry: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None