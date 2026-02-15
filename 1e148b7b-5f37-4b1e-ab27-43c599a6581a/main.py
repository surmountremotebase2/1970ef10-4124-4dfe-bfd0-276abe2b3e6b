from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES F (THE MACHINE GUN) ---
        # MODE: CASH ONLY (No SGOV)
        # Objective: Max Speed. Cuts trade count in half by sitting in Cash.
        
        # ROSTER: Nitro 7
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "BITU", "SILJ"]
        # Safety list is technically not needed for holding, but used for logic if needed.
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390  
        self.mom_len = 40      
        self.safe_len = 20     
        self.atr_period = 14
        
        # GOVERNOR: 2 Days (156 bars)
        self.trend_len = 156   
        
        # LOCKOUT: 0 (Disabled)
        self.system_lockout_counter = 0
        self.lockout_duration = 0 
        
        self.primary_asset = None
        self.entry_price = None
        self.peak_price = None
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
        if not ticker_data: return 0
        df = pd.DataFrame(ticker_data)
        tr = pd.concat([df['high'] - df['low'], 
                        np.abs(df['high'] - df['close'].shift()), 
                        np.abs(df['low'] - df['close'].shift())], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period).mean().iloc[-1]

    def calculate_momentum(self, history, length):
        if len(history) >= length:
            return (history[-1]["close"] / history[-length]["close"]) - 1
        return -999 

    def run(self, data):
        d = data["ohlcv"]
        if not d: return None
        
        if not self.debug_printed:
            log(f"NITRO SERIES F: CASH MODE. Targets: {self.tickers}")
            self.debug_printed = True

        # 1. GOVERNOR CHECK (2-Day Trend)
        spy_hist = self.get_history(d, self.spy)
        if self.calculate_momentum(spy_hist, self.trend_len) < 0:
            if self.primary_asset is None: 
                # RETURN EMPTY = 100% CASH
                return TargetAllocation({})

        # 2. VXX SHIELD
        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if vix_data[-1]["close"] > vix_ma:
                self.primary_asset = None
                return TargetAllocation({})

        # 3. SCORING & SELECTION
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]
        
        # A. ENTRY LOGIC
        if self.primary_asset is None:
            if scores[leader] > 0:
                self.primary_asset = leader
                self.entry_price = self.get_history(d, leader)[-1]["close"]
                self.peak_price = self.entry_price
                log(f"ENTRY: {leader} at {self.entry_price}")
                return TargetAllocation({leader: 1.0})
            else:
                return TargetAllocation({})

        # B. MANAGEMENT LOGIC
        p_hist = self.get_history(d, self.primary_asset)
        if p_hist:
            curr = p_hist[-1]["close"]
            self.peak_price = max(self.peak_price, curr)
            atr = self.calculate_atr(p_hist) or (curr * 0.02)
            
            # STOP LOSS (4.0x) or TRAILING STOP (8.0x)
            if curr <= self.entry_price - (4.0 * atr) or curr <= self.peak_price - (8.0 * atr):
                log(f"EXIT: {self.primary_asset} Stop Hit. Moving to CASH.")
                self.primary_asset = None
                return TargetAllocation({}) # EMPTY = CASH

            return TargetAllocation({self.primary_asset: 1.0})
            
        return None