from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE ---
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VIXY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390  
        self.mom_len = 40      
        self.safe_len = 20     
        self.atr_period = 14   
        
        # --- STATE MACHINE ---
        self.primary_asset = None
        self.secondary_asset = None
        self.entry_price = None
        self.peak_price = None
        self.rotation_stage = 0 

    @property
    def interval(self):
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
        return -999 

    def run(self, data):
        d = data["ohlcv"]
        holdings = data["holdings"]
        
        # --- DEBUGGING LOG ---
        # This will prove the code is running in the logs
        # log(f"Running... VIXY Data Points: {len(d.get(self.vixy, []))}")

        # 1. REGIME CHECK (With Fail-Safe)
        is_defensive = False
        
        # If we have enough data, check the MA
        if self.vixy in d and len(d[self.vixy]) >= self.vix_ma_len:
            vix_closes = [x["close"] for x in d[self.vixy][-self.vix_ma_len:]]
            vix_ma = sum(vix_closes) / len(vix_closes)
            current_vix = d[self.vixy][-1]["close"]
            if current_vix > vix_ma:
                is_defensive = True
        
        # If we are MISSING data, don't freeze. Default to Offensive (Nitro) to start testing.
        elif self.vixy not in d:
            log("WARNING: VIXY data missing. Defaulting to OFFENSIVE.")
            is_defensive = False 

        # --- EXECUTE REGIME ---
        if is_defensive:
            # DEFENSIVE LOGIC
            self.primary_asset = None
            self.secondary_asset = None
            self.rotation_stage = 0
            
            best_safety = "SGOV"
            best_score = -999
            for s in self.safety:
                score = self.calculate_momentum(d, s, self.safe_len)
                if score > best_score:
                    best_score = score
                    best_safety = s
            
            return TargetAllocation({best_safety: 1.0})

        else:
            # OFFENSIVE LOGIC (Nitro)
            scores = {}
            for t in self.tickers:
                scores[t] = self.calculate_momentum(d, t, self.mom_len)
            
            ranked_tickers = sorted(scores, key=scores.get, reverse=True)
            leader = ranked_tickers[0]
            follower = ranked_tickers[1] if len(ranked_tickers) > 1 else ranked_tickers[0]

            # Initialize Trade
            if self.primary_asset is None:
                self.primary_asset = leader
                self.secondary_asset = follower
                # Use current price if available, else skip this tick
                if leader in d:
                    self.entry_price = d[leader][-1]["close"]
                    self.peak_price = self.entry_price
                    self.rotation_stage = 0
                    return TargetAllocation({leader: 1.0})
                else:
                    return None

            # Manage Trade (Rotation)
            if self.primary_asset in d:
                current_price = d[self.primary_asset][-1]["close"]
                self.peak_price = max(self.peak_price, current_price)
                atr = self.calculate_atr(d[self.primary_asset])
                profit_pct = (current_price / self.entry_price) - 1
                
                # Stop Loss (1.5x ATR)
                if current_price <= self.entry_price - (1.5 * atr):
                    log(f"STOP: Initial 1.5x ATR hit on {self.primary_asset}")
                    self.primary_asset = None
                    self.rotation_stage = 0
                    return TargetAllocation({}) 

                # Stage 0 -> 1 (+20% Gain)
                if self.rotation_stage == 0 and profit_pct >= 0.20:
                    self.rotation_stage = 1
                    self.secondary_asset = follower 
                    log(f"ROTATION: +20% Gain. Buying {self.secondary_asset}")
                    return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})

                # Stage 1 -> 2 (Trailing Stop)
                elif self.rotation_stage == 1:
                    if current_price <= self.peak_price - (3 * atr):
                        self.rotation_stage = 2
                        log(f"ROTATION: Trail Hit. Loading {self.secondary_asset}")
                        return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})
                
                # Stage 2 -> 3 (Full Switch)
                elif self.rotation_stage == 2:
                    if current_price <= self.entry_price:
                        self.primary_asset = self.secondary_asset
                        self.secondary_asset = ranked_tickers[2]
                        self.entry_price = d[self.primary_asset][-1]["close"]
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