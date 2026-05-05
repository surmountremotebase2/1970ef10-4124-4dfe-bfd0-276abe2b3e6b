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
        self.max_allocation = 1.0  # 100% allocation
       
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None
       
        # T+1 Settlement Lockout
        self.lockout_date = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 78: return 0
        df = pd.DataFrame(history)
       
        # VWAP calculation (12-period)
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
       
        # RVOL calculation (Current volume vs 20-period average)
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
       
        # Macro Trend Check
        sma_macro = df['close'].mean()
       
        # Asset must be above VWAP and the rolling SMA
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
       
        # Extract the current date from the active bar
        current_date = None
        for t in self.tickers:
            if t in d[-1]:
                # Slicing the Surmount string to isolate 'YYYY-MM-DD'
                current_date = d[-1][t]["date"][:10]
                break
               
        if not current_date: return None
       
        # --- 1. SWING MANAGEMENT ---
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
           
            cp = current_bar["close"]
           
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp
           
            # OFFENSIVE EXIT: Lock in 10% hard gain
            if self.entry_price and cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {self.active_ticker} exit at {cp}. Secured 10%. Locking until next day.")
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
               
                # Trigger the T+1 settlement lockout for the remainder of the calendar day
                self.lockout_date = current_date
                return TargetAllocation({})

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {self.active_ticker} exit at {cp}. Peak was {self.peak_price}. Locking until next day.")
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
               
                # Trigger the T+1 settlement lockout for the remainder of the calendar day
                self.lockout_date = current_date
                return TargetAllocation({})
           
            return None

        # --- 1.5 SETTLEMENT LOCKOUT CHECK ---
        # If the bot exited a trade today, it is blocked from entering a new one until tomorrow.
        if self.lockout_date == current_date:
            return None

        # --- 2. PREDATORY SELECTION ---
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
           
            log(f"SWING ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Entry: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None