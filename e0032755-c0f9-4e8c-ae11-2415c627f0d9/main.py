from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- TQQQ 3-TRANCHE INTRADAY SPRINT ---
        self.tickers = ["TQQQ"]
        
        # --- PARAMETERS ---
        self.vwap_len = 12 # 60 minutes (12 * 5min bars)
        self.vol_ma_len = 12 # 60 minutes for average volume baseline
        self.rvol_threshold = 1.5 # Requires 50% more volume than average to enter
        self.trailing_stop_pct = 0.025 # 2.5% hard defense
        self.tranche_weight = 0.33 # Deploying exactly 1/3 of capital per trade
        
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
        return self.tickers

    def get_history(self, d, ticker):
        history = []
        for bar in d:
            if ticker in bar:
                history.append(bar[ticker])
        return history

    def calculate_vwap_and_rvol(self, history, vwap_length, vol_length):
        if len(history) < max(vwap_length, vol_length):
            return None, None
            
        df = pd.DataFrame(history)
        
        # VWAP Calculation
        recent_df = df.tail(vwap_length)
        q = recent_df['volume']
        p = recent_df['close']
        vwap = (p * q).sum() / q.sum()
        
        # RVOL Calculation
        current_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].tail(vol_length).mean()
        rvol = current_vol / avg_vol if avg_vol > 0 else 0
        
        return vwap, rvol

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # 0. INITIALIZATION: Default to pure cash
        if not self.initial_allocation_done:
            self.initial_allocation_done = True
            return TargetAllocation({}) # Empty dict = 100% Cash

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
                # Liquidate to pure cash
                return TargetAllocation({})
            
            # Hold position silently
            return None

        # 2. ENTRY LOGIC (Offense)
        vwap, rvol = self.calculate_vwap_and_rvol(tqqq_hist, self.vwap_len, self.vol_ma_len)
        
        if vwap and rvol and not self.active_trade:
            # Entry condition: Price > VWAP AND Volume > 1.5x Average
            if current_price > vwap and rvol >= self.rvol_threshold:
                self.active_trade = True
                self.entry_price = current_price
                self.peak_price = current_price
                log(f"ENTRY: TQQQ Buy Signal at {current_price}. VWAP: {vwap:.2f}, RVOL: {rvol:.2f}")
                
                # Deploy exactly one tranche, leave the rest unallocated (cash)
                return TargetAllocation({self.tickers[0]: self.tranche_weight})

        # Default fallback: Wait in silence (maintains cash if flat)
        return None