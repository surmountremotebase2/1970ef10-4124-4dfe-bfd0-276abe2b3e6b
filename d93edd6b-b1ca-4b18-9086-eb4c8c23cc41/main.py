from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE (The Scout List) ---
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]
        
        # --- SAFETY TRINITY ---
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VIXY"

        # --- PARAMETERS ---
        # 5-min bars: ~78 bars/day * 5 days = 390 bars
        self.vix_ma_len = 390  
        self.mom_len = 40 # 40-day Momentum for Nitro
        self.safe_len = 20 # 20-day Momentum for Safety
        self.atr_period = 14 # ATR Period
        
        # --- STATE MACHINE ---
        # We track the Primary (Leader) and Secondary (Follower) assets
        self.primary_asset = None
        self.secondary_asset = None
        
        self.entry_price = None
        self.peak_price = None
        self.rotation_stage = 0 
        # Stage 0: 100% Primary
        # Stage 1: 66% Primary / 33% Secondary
        # Stage 2: 33% Primary / 66% Secondary
        # Stage 3: Full Rotation (Secondary becomes new Primary)

    @property
    def interval(self):
        # The 5-Minute Heartbeat
        return "5min"

    @property
    def assets(self):
        return self.tickers + self.safety + [self.vixy]

    def calculate_atr(self, ticker_data):
        df = pd.DataFrame(ticker_data)
        high_low = df['high'] - df['low']
        high_cp = np.abs(df['high'] - df['close'].shift())
        low_cp = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period).mean().iloc[-1]

    def calculate_momentum(self, d, ticker, length):
        if ticker in d and len(d[ticker]) >= length:
            return (d[ticker][-1]["close"] / d[ticker][-length]["close"]) - 1
        return -999 # Return low score if data missing

    def run(self, data):
        d = data["ohlcv"]
        holdings = data["holdings"]
        
        # 1. CHECK DATA & REGIME (VIXY)
        if self.vixy not in d or len(d[self.vixy]) < self.vix_ma_len:
            return None
            
        vix_closes = [x["close"] for x in d[self.vixy][-self.vix_ma_len:]]
        vix_ma = sum(vix_closes) / len(vix_closes)
        current_vix = d[self.vixy][-1]["close"]
        
        # --- REGIME: DEFENSIVE (RED LIGHT) ---
        if current_vix > vix_ma:
            log("Regime: DEFENSIVE - VIXY Spike")
            # Reset Rotation Logic on Defense
            self.primary_asset = None
            self.secondary_asset = None
            self.rotation_stage = 0
            
            # Pick Best Safety Asset
            best_safety = "SGOV"
            best_score = -999
            for s in self