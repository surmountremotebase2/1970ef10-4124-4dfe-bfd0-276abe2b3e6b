from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE (SERIES F - THE 300% MACHINE GUN) ---
        # TARGET: >$25k Accounts Only. High Frequency Rotation.
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "SILJ", "BITU"]
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390  
        self.mom_len = 40 # Fast Entry (3.5 Hours)
        self.trend_len = 390 # Market Trend (5 Days)
        self.safe_len = 20     
        self.atr_period = 14
        
        # COOLDOWN: 30 Minutes (Rapid Fire Re-entry)
        self.cooldown_bars = 6 
        
        # --- STATE MACHINE ---
        self.primary_asset = None
        self.secondary_asset = None
        self.entry_price = None
        self.peak_price = None
        self.rotation_stage = 0 
        self.last_stop_index = {} 
        self.debug_printed = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers + self.safety + [self.vixy, self.spy]

    def get_history(self, d, ticker):
        history = []
        for bar in d:
            if ticker in bar:
                history.append(bar[ticker])
        return history

    def calculate_atr(self, ticker_data):
        if not ticker_data:
            return 0
        df = pd.DataFrame(ticker_data)
        high_low = df['high'] - df['low']
        high_cp = np.abs(df['high'] - df['close'].shift())
        low_cp = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        return tr.rolling(window=