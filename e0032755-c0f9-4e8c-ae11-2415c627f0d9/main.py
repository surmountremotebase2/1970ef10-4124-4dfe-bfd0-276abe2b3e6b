from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- TQQQ 3-TRANCHE INTRADAY SPRINT ---
        self.tickers = ["TQQQ"]
        self.safety = ["SGOV"] # Using your cash-equivalent proxy
        
        # --- PARAMETERS ---
        self.vwap_len = 12 # 60 minutes (12 * 5min bars)
        self.trailing_stop_pct = 0.025 # 2.5% hard defense
        self.tranche_weight = 0.33 # Deploying exactly 1/3 of capital per trade
        
        # --- STATE TRACKING ---
        self.active_trade = False
        self.entry_price = None
        self.peak_price = None

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers + self.safety

    def get_history(self, d, ticker):
        # Using your proven data extraction method
        history = []
        for bar in d:
            if ticker in bar:
                history.append(bar[ticker])
        return history

    def calculate_vwap(self, history, length):
        if len(history) < length:
            return None
        # Convert the isolated history into a Pandas DataFrame for math operations
        df = pd.DataFrame(history[-length:])
        
        # VWAP Formula: Sum(Price * Volume) / Sum(Volume)
        q = df['volume']
        p = df['close']
        vwap = (p * q).sum() / q.sum()
        return vwap

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        tqqq_hist = self.get_history(d, self.tickers[0])
        if not tqqq_hist: return None
        
        current_bar = tqqq_hist[-1]
        current_price = current_bar["close"]
        
        # 1. MANAGEMENT LOGIC (Defense First)
        if self.active_trade:
            # Update high water mark to tighten the trailing stop
            self.peak_price = max(self.peak_price, current_price)
            
            # The 2.5% Trailing Stop Trigger
            if current_price <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"EXIT: TQQQ Trailing Stop Hit at {current_price}. Peak was {self.peak_price}.")
                self.active_trade = False
                self.entry_price = None
                self.peak_price = None
                # Rotate back to cash equivalent to clear settlement
                return TargetAllocation({"SGOV": 1.0})
            
            # Maintain the 1/3 tranche allocation if safe
            return TargetAllocation({self.tickers[0]: self.tranche_weight, "SGOV": 1.0 - self.tranche_weight})

        # 2. ENTRY LOGIC (Offense)
        vwap = self.calculate_vwap(tqqq_hist, self.vwap_len)
        
        if vwap and not self.active_trade:
            # Entry condition: Current price crosses above the 60-minute VWAP
            if current_price > vwap:
                self.active_trade = True
                self.entry_price = current_price
                self.peak_price = current_price
                log(f"ENTRY: TQQQ Buy Signal at {current_price}. VWAP: {vwap:.2f}")
                
                # Deploy exactly one tranche, keep the rest in cash
                return TargetAllocation({self.tickers[0]: self.tranche_weight, "SGOV": 1.0 - self.tranche_weight})

        # Default fallback: Hold cash
        return TargetAllocation({"SGOV": 1.0})