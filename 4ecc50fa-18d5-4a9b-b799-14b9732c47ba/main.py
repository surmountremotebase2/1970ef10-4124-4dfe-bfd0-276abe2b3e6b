from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (SYNTHETIC 15-MIN + 50/50 SPLIT) ---
        # TIMEFRAME FIX: Interval set to 5m for platform compatibility.
        # LOGIC: Gated to execute only every 15 minutes to eliminate whipsaw.
        # WEIGHTING: Resilient 50/50 allocation split.
        
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "BITU"]
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX"
        self.spy = "SPY"

        # --- PARAMETERS (5-Minute Base Math) ---
        self.vix_ma_len = 390 # 5 Days
        self.mom_len = 40 # Momentum Window
        self.trend_len = 156 # 2 Days
        self.lockout_duration = 39 # 3.5 Hours
        self.atr_period = 14 
        
        self.system_lockout_counter = 0
        self.held_assets = [] 
        self.entry_prices = {}
        self.peak_prices = {}
        self.debug_printed = False
        self.vxx_shield_active = False
        
        # --- SYNTHETIC 15m VARIABLES ---
        self.bar_counter = 0
        self.current_alloc = {"SGOV": 1.0}

    @property
    def interval(self):
        # Kept at 5min to bypass platform limitations
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
        
        self.bar_counter += 1
        
        if not self.debug_printed:
            log("NITRO K: Synthetic 15-Min Engine. 50/50 Split Active.")
            self.debug_printed = True

        # 1. GLOBAL LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            self.current_alloc = {"SGOV": 1.0}
            return TargetAllocation(self.current_alloc)

        # --- SYNTHETIC 15-MINUTE GATEKEEPER ---
        if self.bar_counter % 3 != 0:
            return TargetAllocation(self.current_alloc)

        # --- CORE DECISION LOGIC ---

        # 2. GOVERNOR CHECK (Entry-Only)
        spy_hist = self.get_history(d, self.spy)
        governor_blocks_entries = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 3. VXX SHIELD (With Hysteresis Buffer)
        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            current_vix = vix_data[-1]["close"]

            if not self.vxx_shield_active and current_vix > vix_ma:
                self.vxx_shield_active = True
                log("VXX Shield ENGAGED.")
            elif self.vxx_shield_active and current_vix < (vix_ma * 0.98):
                self.vxx_shield_active = False
                log("VXX Shield DISENGAGED.")

            if self.vxx_shield_active:
                self.held_assets = []
                self.current_alloc = {"SGOV": 1.0}
                return TargetAllocation(self.current_alloc)

        # 4. SCORING & SELECTION (Top 2 for 50/50 Split)
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        sorted_leaders = sorted(scores, key=scores.get, reverse=True)
        top_2 = [sorted_leaders[0], sorted_leaders[1]]

        # A. ENTRY LOGIC
        if not self.held_assets:
            if governor_blocks_entries:
                self.current_alloc = {"SGOV": 1.0}
                return TargetAllocation(self.current_alloc)
            
            valid_leaders = [t for t in top_2 if scores[t] > 0]
            
            if len(valid_leaders) == 2:
                self.held_assets = valid_leaders
                self.current_alloc = {valid_leaders[0]: 0.5, valid_leaders[1]: 0.5}
                self.entry_prices = {t: self.get_history(d, t)[-1]["close"] for t in valid_leaders}
                self.peak_prices = {t: self.get_history(d, t)[-1]["close"] for t in valid_leaders}
                log(f"ENTRY: 50/50 Split -> {valid_leaders[0]} & {valid_leaders[1]}")
                return TargetAllocation(self.current_alloc)
            
            elif len(valid_leaders) == 1:
                l = valid_leaders[0]
                self.held_assets = [l]
                self.current_alloc = {l: 0.5, "SGOV": 0.5}
                self.entry_prices = {l: self.get_history(d, l)[-1]["close"]}
                self.peak_prices = {l: self.get_history(d, l)[-1]["close"]}
                log(f"ENTRY: 50/50 Split -> {l} & SGOV")
                return TargetAllocation(self.current_alloc)
            else:
                self.current_alloc = {"SGOV": 1.0}
                return TargetAllocation(self.current_alloc)

        # B. MANAGEMENT LOGIC
        hit_stop = False
        for asset in list(self.held_assets):
            p_hist = self.get_history(d, asset)
            if p_hist:
                curr = p_hist[-1]["close"]
                self.peak_prices[asset] = max(self.peak_prices.get(asset, curr), curr)
                atr = self.calculate_atr(p_hist) or (curr * 0.02)
                
                entry_p = self.entry_prices.get(asset, curr)
                peak_p = self.peak_prices.get(asset, curr)
                
                # STOP LOSS (7.0x) or TRAILING STOP (12.0x)
                if curr <= entry_p - (7.0 * atr) or curr <= peak_p - (12.0 * atr):
                    log(f"EXIT: {asset} Stop/Trail Hit. Lockdown Engaged.")
                    hit_stop = True
        
        # If any asset hits its stop, the entire engine locks down
        if hit_stop:
            self.system_lockout_counter = self.lockout_duration
            self.held_assets = []
            self.current_alloc = {"SGOV": 1.0}
            return TargetAllocation(self.current_alloc)

        return TargetAllocation(self.current_alloc)