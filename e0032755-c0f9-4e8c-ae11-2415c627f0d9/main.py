from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- TQQQ 3-TRANCHE INTRADAY SPRINT ---
        self.tickers = ["TQQQ"]
        self.safety = ["SGOV"] 
        
        # --- PARAMETERS ---
        self.vwap_len = 12 
        self.trailing_stop_pct = 0.025 
        self.tranche_weight = 0.33 
        
        # --- STATE TRACKING ---
        self.active_trade = False
        self.entry_price = None
        self.peak_price = None
        self.initial_allocation_done = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers + self.safety

    def get_history(self, d, ticker):
        history = []
        for bar in d:
            if ticker in bar:
                history.append(bar[ticker])
        return history

    def calculate_vwap(self, history, length):
        if len(history) < length:
            return None
        df = pd.DataFrame(history[-length:])
        q = df['volume']
        p = df['close']
        vwap = (p * q).sum() / q.sum()
        return vwap

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # 0. INITIALIZATION: Park everything in cash on the very first run
        if not self.initial_allocation_done:
            self.initial_allocation_done = True
            return TargetAllocation({"SGOV": 1.0})

        tqqq_hist = self.get_history(d, self.tickers[0])
        if not tqqq_hist: return None
        
        current_bar = tqqq_hist[-1]
        current_price = current_bar["close"]
        
        # 1. MANAGEMENT LOGIC (Defense First)
        if self.active_trade:
            self.peak_price = max(self.peak_price, current_price)
            
            # Stop Loss Trigger
            if current_price <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"EXIT: TQQQ Trailing Stop Hit at {current_price}. Peak was {self.peak_price}.")
                self.active_trade = False
                self.entry_price = None
                self.peak_price = None
                return TargetAllocation({"SGOV": 1.0})
            
            # CRITICAL FIX: Do absolutely nothing while holding the position.
            return None

        # 2. ENTRY LOGIC (Offense)
        vwap = self.calculate_vwap(tqqq_hist, self.vwap_len)
        
        if vwap and not self.active_trade:
            if current_price > vwap:
                self.active_trade = True
                self.entry_price = current_price
                self.peak_price = current_price
                log(f"ENTRY: TQQQ Buy Signal at {current_price}. VWAP: {vwap:.2f}")
                
                return TargetAllocation({self.tickers[0]: self.tranche_weight, "SGOV": 1.0 - self.tranche_weight})

        # Default fallback: Wait in silence
        return None