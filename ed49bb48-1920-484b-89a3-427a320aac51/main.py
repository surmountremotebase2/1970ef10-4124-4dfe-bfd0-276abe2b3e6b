from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- DIAGNOSTIC UNIVERSE ---
        # We are using only the most liquid, standard assets to test the pipe.
        # Temporarily removed: IBIT, BITU, SILJ (Potential data blockers)
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM"]
        
        # --- SAFETY ---
        self.safety = ["SGOV", "IAU", "DBMF"]
        
        # Switched VIXY -> VXX (Better availability)
        self.vixy = "VXX"

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
        
        # Debug flag to prevent log spam
        self.debug_printed = False

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
        # Modified for Diagnostic: Looser check
        if ticker in d and len(d[ticker]) > 0:
            # If we don't have full history, use what we have
            lookback = min(len(d[ticker])-1, length)
            return (d[ticker][-1]["close"] / d[ticker][-lookback]["close"]) - 1
        return -999 

    def run(self, data):
        d = data["ohlcv"]
        holdings = data["holdings"]
        
        # --- CRITICAL DIAGNOSTIC LOG ---
        # This will tell us ONCE what assets are actually loaded.
        if not self.debug_printed:
            log(f"DIAGNOSTIC: Loaded Assets -> {list(d.keys())}")
            self.debug_printed = True

        # 1. REGIME CHECK (With VXX)
        is_defensive = False
        
        if self.vixy in d and len(d[self.vixy]) >= self.vix_ma_len:
            vix_closes = [x["close"] for x in d[self.vixy][-self.vix_ma_len:]]
            vix_ma = sum(vix_closes) / len(vix_closes)
            current_vix = d[self.vixy][-1]["close"]
            if current_vix > vix_ma:
                is_defensive = True
        elif self.vixy not in d:
            # Fail Silent but Default to Offensive
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
                
                # Check if Leader Data Exists
                if leader in d:
                    self.entry_price = d[leader][-1]["close"]
                    self.peak_price = self.entry_price
                    self.rotation_stage = 0
                    log(f"TRADE ENTRY: {leader} at {self.entry_price}")
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