from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE ---
        # 1. Scout List (High Beta)
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "SILJ"]
        # 2. Safety Trinity (Shield)
        self.safety = ["SGOV", "IAU", "DBMF"]
        # 3. Regime Filter (VXX is more reliable than VIXY)
        self.vixy = "VXX" 

        # --- PARAMETERS (CALIBRATED) ---
        self.vix_ma_len = 390 # 5 Days of 5-min bars
        self.mom_len = 40 # 40-Bar Momentum
        self.safe_len = 20 # Safety Momentum
        self.atr_period = 14   
        
        # --- STATE MACHINE ---
        self.primary_asset = None
        self.secondary_asset = None
        self.entry_price = None
        self.peak_price = None
        self.rotation_stage = 0 
        
        self.debug_printed = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers + self.safety + [self.vixy]

    # --- THE CRITICAL FIX (Unpacker) ---
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
        return tr.rolling(window=self.atr_period).mean().iloc[-1]

    def calculate_momentum(self, history, length):
        if len(history) >= length:
            return (history[-1]["close"] / history[-length]["close"]) - 1
        elif len(history) > 0:
            return (history[-1]["close"] / history[0]["close"]) - 1
        return -999 

    def run(self, data):
        # d is a LIST of time snapshots
        d = data["ohlcv"]
        
        if not d:
            return None
            
        # 1. DIAGNOSTIC LOG (Just to confirm assets loaded)
        if not self.debug_printed:
            try:
                log(f"DIAGNOSTIC: Assets found -> {list(d[-1].keys())}")
            except:
                pass
            self.debug_printed = True

        # 2. VXX REGIME CHECK (The Shield)
        vix_data = self.get_history(d, self.vixy)
        is_defensive = False
        
        if len(vix_data) >= self.vix_ma_len:
            vix_closes = [x["close"] for x in vix_data[-self.vix_ma_len:]]
            vix_ma = sum(vix_closes) / len(vix_closes)
            current_vix = vix_data[-1]["close"]
            
            # TRIGGER: If VXX is above its 5-day average -> DEFENSE
            if current_vix > vix_ma:
                is_defensive = True
        elif not vix_data:
            # If VXX missing, assume Offensive (Risk-On)
            is_defensive = False

        # --- EXECUTE REGIME ---
        if is_defensive:
            # --- DEFENSIVE MODE (Safety Trinity) ---
            self.primary_asset = None
            self.secondary_asset = None
            self.rotation_stage = 0
            
            best_safety = "SGOV"
            best_score = -999
            for s in self.safety:
                s_hist = self.get_history(d, s)
                score = self.calculate_momentum(s_hist, self.safe_len)
                if score > best_score:
                    best_score = score
                    best_safety = s
            
            return TargetAllocation({best_safety: 1.0})

        else:
            # --- OFFENSIVE MODE (Nitro Rotator) ---
            scores = {}
            histories = {t: self.get_history(d, t) for t in self.tickers}
            
            for t in self.tickers:
                scores[t] = self.calculate_momentum(histories[t], self.mom_len)
            
            # Rank Assets
            ranked_tickers = sorted(scores, key=scores.get, reverse=True)
            leader = ranked_tickers[0]
            follower = ranked_tickers[1] if len(ranked_tickers) > 1 else ranked_tickers[0]

            # A. ENTER NEW TRADE
            if self.primary_asset is None:
                if len(histories[leader]) > 0:
                    self.primary_asset = leader
                    self.secondary_asset = follower
                    self.entry_price = histories[leader][-1]["close"]
                    self.peak_price = self.entry_price
                    self.rotation_stage = 0
                    log(f"ENTRY: {leader} at {self.entry_price}")
                    return TargetAllocation({leader: 1.0})
                else:
                    return None

            # B. MANAGE EXISTING TRADE
            p_hist = histories.get(self.primary_asset, [])
            if p_hist:
                current_price = p_hist[-1]["close"]
                self.peak_price = max(self.peak_price, current_price)
                
                # ATR CALCULATION
                atr = self.calculate_atr(p_hist)
                if atr == 0: atr = current_price * 0.02 # Fallback
                
                profit_pct = (current_price / self.entry_price) - 1
                
                # --- STOP LOSS (WIDENED TO 2.5x) ---
                # Reduced noise sensitivity to lower trade count
                if current_price <= self.entry_price - (2.5 * atr):
                    log(f"STOP: 2.5x ATR Hit on {self.primary_asset}")
                    self.primary_asset = None
                    self.rotation_stage = 0
                    return TargetAllocation({}) 

                # --- ROTATION LOGIC ---
                
                # Stage 1: Hit +20% Gain -> Rotate 1/3
                if self.rotation_stage == 0 and profit_pct >= 0.20:
                    self.rotation_stage = 1
                    self.secondary_asset = follower 
                    log(f"ROTATION: +20% Gain. Buying {self.secondary_asset}")
                    return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})

                # Stage 2: Hit Trailing Stop (Widened to 3.5x) -> Rotate 1/3
                elif self.rotation_stage == 1:
                    # Trailing stop widened to 3.5x to let winners run
                    if current_price <= self.peak_price - (3.5 * atr):
                        self.rotation_stage = 2
                        log(f"ROTATION: Trail Hit. Loading {self.secondary_asset}")
                        return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})
                
                # Stage 3: Full Switch if Primary Fails
                elif self.rotation_stage == 2:
                    if current_price <= self.entry_price:
                        self.primary_asset = self.secondary_asset
                        self.secondary_asset = ranked_tickers[2]
                        # Reset for new asset
                        p_new_hist = self.get_history(d, self.primary_asset)
                        if p_new_hist:
                            self.entry_price = p_new_hist[-1]["close"]
                            self.peak_price = self.entry_price
                            self.rotation_stage = 0
                            return TargetAllocation({self.primary_asset: 1.0})

                # Maintain Positions
                if self.rotation_stage == 0:
                    return TargetAllocation({self.primary_asset: 1.0})
                elif self.rotation_stage == 1:
                    return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})
                elif self.rotation_stage == 2:
                    return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})

        return None