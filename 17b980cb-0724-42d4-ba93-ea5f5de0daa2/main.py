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
            for s in self.safety:
                score = self.calculate_momentum(d, s, self.safe_len)
                if score > best_score:
                    best_score = score
                    best_safety = s
            
            return TargetAllocation({best_safety: 1.0})

        # --- REGIME: OFFENSIVE (GREEN LIGHT) ---
        
        # 2. IDENTIFY RANKS (Scout the Field)
        # Rank all Nitro assets by 40-day momentum
        scores = {}
        for t in self.tickers:
            scores[t] = self.calculate_momentum(d, t, self.mom_len)
        
        # Sort tickers by score (Highest to Lowest)
        ranked_tickers = sorted(scores, key=scores.get, reverse=True)
        
        # Identify the Leader (#1) and the Follower (#2)
        leader = ranked_tickers[0]
        follower = ranked_tickers[1] if len(ranked_tickers) > 1 else ranked_tickers[0]

        # 3. INITIALIZE TRADE (If Empty)
        if self.primary_asset is None:
            self.primary_asset = leader
            self.secondary_asset = follower
            self.entry_price = d[leader][-1]["close"]
            self.peak_price = self.entry_price
            self.rotation_stage = 0
            log(f"NEW TRADE: Starting 100% in {leader}")
            return TargetAllocation({leader: 1.0})

        # 4. MANAGE TRADE (Rotation Logic)
        current_price = d[self.primary_asset][-1]["close"]
        self.peak_price = max(self.peak_price, current_price)
        atr = self.calculate_atr(d[self.primary_asset])
        
        # Calculate Profit
        profit_pct = (current_price / self.entry_price) - 1
        
        # --- STOP LOSS LOGIC (Defense) ---
        # Initial 1.5x ATR Stop (Hard Floor)
        if current_price <= self.entry_price - (1.5 * atr):
            log(f"STOP: Initial 1.5x ATR hit on {self.primary_asset}. Resetting.")
            self.primary_asset = None
            self.rotation_stage = 0
            return TargetAllocation({}) # Cash out, look for new trade next tick

        # --- ROTATION LOGIC (Offense) ---
        
        # Stage 0 -> Stage 1: Hit +20% Gain
        # Action: Sell 1/3 Primary, Buy 1/3 Secondary
        if self.rotation_stage == 0 and profit_pct >= 0.20:
            self.rotation_stage = 1
            # Update Secondary to current best follower
            self.secondary_asset = follower 
            log(f"ROTATION START: +20% Hit. Buying 33% {self.secondary_asset}")
            return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})

        # Stage 1 -> Stage 2: Trailing Stop (3x ATR) Hit
        # Action: Sell next 1/3 Primary, Buy more Secondary
        elif self.rotation_stage == 1:
            # Check 3x ATR Trail
            if current_price <= self.peak_price - (3 * atr):
                self.rotation_stage = 2
                log(f"ROTATION MID: 3x ATR Trail Hit. Moving to 66% {self.secondary_asset}")
                return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})
        
        # Stage 2 -> Stage 3: Full Handoff
        # If Secondary Momentum > Primary Momentum by significant margin OR Primary hits final stop
        elif self.rotation_stage == 2:
            # If Primary falls below entry (Moonshot failed) -> Full Rotation
            if current_price <= self.entry_price:
                log(f"ROTATION COMPLETE: {self.primary_asset} stopped out. Full switch to {self.secondary_asset}")
                self.primary_asset = self.secondary_asset
                self.secondary_asset = ranked_tickers[2] # Next in line
                self.entry_price = d[self.primary_asset][-1]["close"]
                self.peak_price = self.entry_price
                self.rotation_stage = 0
                return TargetAllocation({self.primary_asset: 1.0})

        # MAINTAIN CURRENT WEIGHTS (If no triggers hit)
        if self.rotation_stage == 0:
            return TargetAllocation({self.primary_asset: 1.0})
        elif self.rotation_stage == 1:
            return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})
        elif self.rotation_stage == 2:
            return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})

        return None