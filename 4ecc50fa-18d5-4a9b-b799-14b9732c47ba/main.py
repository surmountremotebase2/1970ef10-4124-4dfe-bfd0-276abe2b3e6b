from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (SYNTHETIC 15-MIN + PURE MOMENTUM) ---
        # TIMEFRAME: Interval 5m. Gated to execute every 15 minutes.
        # ALLOCATION: 100% Concentrated.
        # FIX: ATR logic completely removed. Exits driven strictly by momentum.
        
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "BITU"]
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX"
        self.spy = "SPY"

        # --- PARAMETERS (5-Minute Base Math) ---
        self.vix_ma_len = 390 
        self.mom_len = 40 
        self.trend_len = 156 
        self.lockout_duration = 39 
        
        self.system_lockout_counter = 0
        self.primary_asset = None
        self.debug_printed = False
        self.vxx_shield_active = False
        
        self.bar_counter = 0
        self.current_alloc = {"SGOV": 1.0}

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

    def calculate_momentum(self, history, length):
        if len(history) >= length:
            return (history[-1]["close"] / history[-length]["close"]) - 1
        return -999

    def run(self, data):
        d = data["ohlcv"]
        if not d: return None
        
        self.bar_counter += 1
        
        if not self.debug_printed:
            log("NITRO K: Synthetic 15-Min Engine. Pure Momentum Exits Active.")
            self.debug_printed = True

        # 1. GLOBAL LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.system_lockout_counter == 0:
                self.current_alloc = {"SGOV": 1.0}
                return TargetAllocation(self.current_alloc)
            return None 

        # --- SYNTHETIC 15-MINUTE GATEKEEPER ---
        if self.bar_counter % 3 != 0:
            return None 

        # --- CORE DECISION LOGIC ---

        # 2. GOVERNOR CHECK (Now acts as a Hard Exit too)
        spy_hist = self.get_history(d, self.spy)
        spy_momentum = self.calculate_momentum(spy_hist, self.trend_len)
        governor_negative = spy_momentum < 0

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
                if self.primary_asset is not None:
                    log(f"EXIT: VXX Shield forced liquidation of {self.primary_asset}.")
                    self.primary_asset = None
                    self.current_alloc = {"SGOV": 1.0}
                    return TargetAllocation(self.current_alloc)
                return None

        # Check if Governor forces an exit
        if self.primary_asset is not None and governor_negative:
            log(f"EXIT: SPY Governor turned negative. Liquidating {self.primary_asset}.")
            self.system_lockout_counter = self.lockout_duration
            self.primary_asset = None
            self.current_alloc = {"SGOV": 1.0}
            return TargetAllocation(self.current_alloc)

        # 4. SCORING & SELECTION
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]

        # A. ENTRY LOGIC
        if self.primary_asset is None:
            if governor_negative:
                if self.current_alloc.get("SGOV") != 1.0:
                    self.current_alloc = {"SGOV": 1.0}
                    return TargetAllocation(self.current_alloc)
                return None
            
            if scores[leader] > 0:
                self.primary_asset = leader
                self.current_alloc = {leader: 1.0}
                log(f"ENTRY: Pure Momentum -> {leader}")
                return TargetAllocation(self.current_alloc)
            else:
                return None

        # B. MANAGEMENT LOGIC (Pure Momentum)
        if self.primary_asset is not None:
            current_score = scores.get(self.primary_asset, -999)
            
            # EXIT: If the asset's momentum drops below zero
            if current_score < 0:
                log(f"EXIT: {self.primary_asset} momentum died ({current_score:.4f}). Lockdown Engaged.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                self.current_alloc = {"SGOV": 1.0}
                return TargetAllocation(self.current_alloc)

        return None