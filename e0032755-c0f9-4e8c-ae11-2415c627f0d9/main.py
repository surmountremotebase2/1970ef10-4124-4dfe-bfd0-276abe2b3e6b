from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        self.tickers = ["TQQQ"]
        
        self.vwap_len = 12 
        self.vol_ma_len = 12 
        self.rvol_threshold = 1.5 
        self.trailing_stop_pct = 0.025 
        self.tranche_weight = 1.0 
        
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
        
        recent_df = df.tail(vwap_length)
        q = recent_df['volume']
        p = recent_df['close']
        vwap = (p * q).sum() / q.sum()
        
        current_vol = df['volume'].iloc[-1]
        avg_vol = df['volume'].tail(vol_length).mean()
        rvol = current_vol / avg_vol if avg_vol > 0 else 0
        
        return vwap, rvol

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        if not self.initial_allocation_done:
            self.initial_allocation_done = True
            return TargetAllocation({}) 

        tqqq_hist = self.get_history(d, self.tickers[0])
        if not tqqq_hist: return None
        
        current_bar = tqqq_hist[-1]
        current_price = current_bar["close"]
        
        if self.active_trade:
            self.peak_price = max(self.peak_price, current_price)
            
            if current_price <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"EXIT: TQQQ Trailing Stop Hit at {current_price}. Peak was {self.peak_price}.")
                self.active_trade = False
                self.entry_price = None
                self.peak_price = None
                return TargetAllocation({})
            
            return None

        vwap, rvol = self.calculate_vwap_and_rvol(tqqq_hist, self.vwap_len, self.vol_ma_len)
        
        if vwap and rvol and not self.active_trade:
            if current_price > vwap and rvol >= self.rvol_threshold:
                self.active_trade = True
                self.entry_price = current_price
                self.peak_price = current_price
                log(f"ENTRY: TQQQ Buy Signal at {current_price}. VWAP: {vwap:.2f}, RVOL: {rvol:.2f}")
                
                return TargetAllocation({self.tickers[0]: self.tranche_weight})

        return None