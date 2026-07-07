from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # 5-Ticker Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
        
        # Engine Parameters
        self.allocation_size = 0.50 
        self.max_positions = 2      
        
        # Breakout Strategy Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.breakout_tp = 0.10
        self.breakout_stop = 0.08
        
        # Capitulation (Dip) Strategy Parameters
        self.dip_tp = 0.15
        self.dip_stop = 0.03
        
        # Internal Memory Tracker
        self.active_positions = {}
        self.exited_tickers = [] 

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_signal(self, history):
        if len(history) < 200: return 0, None
        df = pd.DataFrame(history)
        
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        open_price = df['open'].iloc[-1]
        
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        sma_macro = df['close'].tail(200).mean() # 2.5 Day Trend
        
        # Calculate 14-Period RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        # Strategy 1: Standard Macro Trend Breakout
        standard_buy = (current_price > vwap) and (current_price > sma_macro) and (rvol >= self.rvol_threshold)
        
        # Strategy 2: Capitulation Dip Buy
        is_green = current_price > open_price
        is_stretched = current_price < (sma_macro * 0.85) # 15% Rubber Band Stretch
        
        dip_buy = is_green and is_stretched and (current_rsi < 20) and (rvol >= 2.5)
        
        if dip_buy:
            return rvol, "dip"
        elif standard_buy:
            return rvol, "breakout"
            
        return 0, None

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        holdings = data.get("holdings", {})
        orders = data.get("orders", [])
        
        # --- FIXED GHOST WORKAROUND ---
        ghost_positions = []
        for order in orders:
            t = order.get("ticker") or order.get("symbol")
            if t not