from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K-OPTIMIZED (STRESS TEST: NO SILVER) ---
        # Objective: Verify organic growth without the Silver rally bias.
        
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "BITU", "UPRO"]
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390  
        self.mom_len = 40      
        self.safe_len = 20     
        self.atr_period = 14
        self.trend_len = 156 # 2-Day Market Context
        
        # Half-Day Lockout (39 bars) to stay compliant with small accounts
        self.system_lockout_counter = 0
        self.lockout_duration = 39 
        
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
            log(f"SERIES K-STRESS TEST: No Silver | Roster: {self.tickers}")
            self.debug_printed = True

        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            return TargetAllocation({"SGOV": 1.0})

        spy_hist = self.get_history(d, self.spy)
        if self.calculate_momentum(spy_hist, self.trend_len) < 0:
            if self.primary_asset is None: return TargetAllocation({"SGOV": 1.0})

        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if vix_data[-1]["close"] > vix_ma:
                self.primary_asset = None
                return TargetAllocation({"SGOV": 1.0})

        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]
        
        if self.primary_asset is None: