from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Macro Asset Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
       
        # Core Engine Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        self.max_allocation = 1.00 
       
        # Rigid State Machine Memory
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None
        
        # Temporal Cooldown Matrix (Resolves Backtest Lag vs Live Recovery)
        self.cooldown_ticker = None
        self.cooldown_bars = 0

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
        
        holdings = data.get("holdings", {})
        
        # --- VARIABLE 1: TEMPORAL COOLDOWN DECAY ---
        # Automatically decrements and clears the backtest lag block after 2 bars
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            if self.cooldown_bars == 0:
                self.cooldown_ticker = None

        # --- VARIABLE 2: PRODUCTION AMNESIA RECOVERY ---
        # If internal state is cash, verify the live broker ledger agrees
        if not self.active_trade and holdings:
            for t in self.tickers:
                if holdings.get(t, 0) > 0:
                    # Check if this asset is currently clearing a backtest exit
                    if t == self.cooldown_ticker:
                        continue 
                    
                    # Legitimate live position found during server restart
                    self.active_trade = True
                    self.active_ticker = t
                    log(f"AMNESIA RECOVERY: Resynced live position for {t}")
                    break

        # --- VARIABLE 3: SWING MANAGEMENT (EXITS) ---
        if self.active_trade and self.active_ticker:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
           
            cp = current_bar["close"]
           
            # Initialize tracking metrics if recovering from a live restart
            if self.peak_price is None or self.entry_price is None:
                self.peak_price = cp
                self.entry_price = cp
                log(f"RECOVERY INITIALIZATION: Bounds set for {self.active_ticker} at {cp}")
           
            if cp > self.peak_price:
                self.peak_price = cp
           
            # Offensive Target: 10% Profit
            if cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT TRIGGERED: Exiting {self.active_ticker} at {cp}.")
                self.cooldown_ticker = self.active_ticker
                self.cooldown_bars = 2 # Restricts amnesia logic for exactly 2 bars
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})

            # Defensive Target: 8% Trailing Stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"TRAILING STOP TRIGGERED: Exiting {self.active_ticker} at {cp}.")
                self.cooldown_ticker = self.active_ticker
                self.cooldown_bars = 2  
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
                return TargetAllocation({})
           
            return None

        # --- VARIABLE 4: PREDATORY SELECTION (ENTRIES) ---
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
           
            log(f"STRATEGY ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Entry Price: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None