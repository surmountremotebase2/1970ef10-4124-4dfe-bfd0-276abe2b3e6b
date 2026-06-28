from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Final Macro Roster (TECL Removed)
        self.tickers = ["GDXU", "SOXL", "UCO", "AGQ"]
        
        # Core Engine Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        self.max_allocation = 1.00
        
        # Internal Memory Trackers
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None
        self.exited_ticker = None # Circuit breaker to handle backtester settlement lag

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        # Ensure we have enough data for the 200-bar SMA lookback
        if len(history) < 200: return 0 
        df = pd.DataFrame(history)
        
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        # Fixed: Hardcoded 200-bar lookback for live execution consistency
        sma_macro = df['close'].tail(200).mean()
        
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        holdings = data.get("holdings", {})
        
        # --- AMNESIA RECOVERY CIRCUIT BREAKER ---
        if not self.active_trade and holdings:
            for t in self.tickers:
                if holdings.get(t, 0) > 0:
                    if t != self.exited_ticker:
                        self.active_trade = True
                        self.active_ticker = t
                        log(f"AMNESIA RECOVERY: Resynced live position for {t}")
                        break

        # --- 1. SWING MANAGEMENT (10% Take-Profit & 8% Trailing Stop) ---
        if self.active_trade and self.active_ticker:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            
            cp = current_bar["close"]
            
            if self.peak_price is None or self.entry_price is None:
                self.peak_price = cp
                self.entry_price = cp
                log(f"RECOVERY INITIALIZATION: Set tracking baseline for {self.active_ticker} at {cp}")
            
            if cp > self.peak_price:
                self.peak_price = cp
            
            # OFFENSIVE EXIT: 10% Target
            if cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {self.active_ticker} exit at {cp}.")
                self.exited_ticker = self.active_ticker 
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {self.active_ticker} exit at {cp}.")
                self.exited_ticker = self.active_ticker 
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
            
            self.exited_ticker = None
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            self.entry_price = d[-1][best_ticker]["close"]
            
            log(f"SWING ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Entry: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None