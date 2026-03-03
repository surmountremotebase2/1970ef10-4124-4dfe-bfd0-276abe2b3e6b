from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (V2 - 15 MINUTE RECALIBRATION) ---
        # ACTION: Shifted to supported 15m interval to eliminate platform KeyError.
        # VAULT: 100% SGOV to prevent T+1 Good Faith Violations.
        
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "BITU", "AGQ"]
        
        # CATEGORY OVERRIDE: Assets allowed to bypass the SPY Governor
        self.uncorrelated_assets = ["AGQ", "UCO", "BITU"]
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX"
        self.spy = "SPY"

        # --- PARAMETERS (Recalibrated for 15min: 26 bars per trading day) ---
        self.vix_ma_len = 26 # 1 Trading Day (Fast Recovery)
        self.mom_len = 14 # ~3.5 Hours (Offensive Engine Lookback)
        self.trend_len = 52 # 2 Trading Days (SPY Trend Filter)
        self.lockout_duration = 26 # 1 Full Trading Day (Anti-Churn Lockout)
        self.atr_period = 14
        
        self.system_lockout_counter = 0
        self.primary_asset = None
        self.entry_price = None
        self.peak_price = None
        self.debug_printed = False

    @property
    def interval(self):
        return "15min"

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
            log(f"NITRO K V2 [15MIN]: Bypass Active. 10.0x ATR Trailer. 100% SGOV Vault.")
            self.debug_printed = True

        # 1. LOCKOUT CHECK (Churn Protection - Now 1 Full Day)
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            return TargetAllocation({"SGOV": 1.0})

        # 2. VXX SHIELD (Hard Defense)
        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if vix_data[-1]["close"] > vix_ma:
                self.primary_asset = None
                return TargetAllocation({"SGOV": 1.0})

        # 3. GOVERNOR BYPASS & SCORING
        valid_tickers = self.tickers.copy()
        
        if self.primary_asset is None:
            spy_hist = self.get_history(d, self.spy)
            if self.calculate_momentum(spy_hist, self.trend_len) < 0:
                # SPY is down. Only score uncorrelated assets.
                valid_tickers = self.uncorrelated_assets
                
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in valid_tickers}
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
                return TargetAllocation({"SGOV": 1.0})

        # B. MANAGEMENT LOGIC
        p_hist = self.get_history(d, self.primary_asset)
        if p_hist:
            curr = p_hist[-1]["close"]
            self.peak_price = max(self.peak_price, curr)
            atr = self.calculate_atr(p_hist) or (curr * 0.02)
            
            # STOP LOSS (4.5x) or TRAILING STOP (10.0x)
            if curr <= self.entry_price - (4.5 * atr) or curr <= self.peak_price - (10.0 * atr):
                log(f"EXIT: {self.primary_asset} Stop/Trail Hit. 1-Day Lockdown Engaged.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                return TargetAllocation({"SGOV": 1.0})

            return TargetAllocation({self.primary_asset: 1.0})