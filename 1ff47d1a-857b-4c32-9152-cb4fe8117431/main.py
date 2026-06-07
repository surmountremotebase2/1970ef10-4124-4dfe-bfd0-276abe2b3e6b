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
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        self.max_allocation = 1.00 
       
        # Internal Memory Tracker
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 78: return 0
        df = pd.DataFrame(history)
       
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
       
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
       
        sma_macro = df['close'].mean()
       
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # --- THE HYBRID STATE TRACKER ---
        # 1. Default to internal memory (Required for Backtesting)
        current_ticker = self.active_ticker
        is_active = self.active_trade
        
        # 2. Live Broker Override (Required for Production Amnesia Recovery)
        holdings = data.get("holdings", {})
        if holdings:
            found_live_asset = False
            for t in self.tickers:
                if holdings.get(t, 0) > 0: 
                    current_ticker = t
                    is_active = True
                    found_live_asset = True
                    break
            
            # If the live feed confirms we hold no roster assets, force cash state
            if not found_live_asset:
                is_active = False
                current_ticker = None

        # --- 1. SWING MANAGEMENT (10% Take-Profit & 8% Trailing Stop) ---
        if is_active and current_ticker:
            current_bar = d[-1].get(current_ticker)
            if not current_bar: return None
           
            cp = current_bar["close"]
           
            # AMNESIA RECOVERY: Restore tracking logic if server restarted
            if self.peak_price is None or self.entry_price is None:
                self.peak_price = cp
                self.entry_price = cp
                log(f"RECOVERY OVERRIDE: Restored tracking for {current_ticker} at {cp}")
           
            if cp > self.peak_price:
                self.peak_price = cp
           
            # OFFENSIVE EXIT: 10% target
            if cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {current_ticker} exit at {cp}.")
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})

            # DEFENSIVE EXIT: 8% stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {current_ticker} exit at {cp}.")
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})
           
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
            best_ticker = max(scores, key=scores.get)
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            self.entry_price = d[-1][best_ticker]["close"]
           
            log(f"SWING ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Entry: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None