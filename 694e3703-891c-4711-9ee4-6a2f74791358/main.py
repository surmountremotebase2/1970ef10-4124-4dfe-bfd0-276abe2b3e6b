from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE (SERIES F) ---
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "SILJ", "BITU"]
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX" 
        
        # *** NEW: THE MARKET GOVERNOR ***
        self.spy = "SPY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390  
        
        # MOMENTUM SETTINGS
        self.mom_len = 40 # Asset Entry: Fast (3 Hours)
        self.trend_len = 390 # Market Trend: Slow (5 Days)
        
        self.safe_len = 20     
        self.atr_period = 14
        self.cooldown_bars = 6 # 30 Mins
        
        # --- STATE MACHINE ---
        self.primary_asset = None
        self.secondary_asset = None
        self.entry_price = None
        self.peak_price = None
        self.rotation_stage = 0 
        self.last_stop_index = {} 
        self.debug_printed = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        # Added self.spy to the list
        return self.tickers + self.safety + [self.vixy, self.spy]

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
        d = data["ohlcv"]
        if not d:
            return None
            
        current_bar_index = len(d)
        
        # 1. DIAGNOSTIC LOG
        if not self.debug_printed:
            try:
                log(f"SERIES F (GOVERNOR): SPY Trend Filter Active")
            except:
                pass
            self.debug_printed = True

        # 2. GLOBAL MARKET CHECK (The Governor)
        # Check SPY Trend (5 Days). If SPY is down, Hard Stop.
        spy_hist = self.get_history(d, self.spy)
        spy_trend = self.calculate_momentum(spy_hist, self.trend_len)
        
        # If SPY is missing or Negative Trend -> FORCE SAFETY
        # This prevents buying dips in a bear market.
        if spy_trend < 0:
            # Check if we are already in a trade
            if self.primary_asset is not None:
                # OPTIONAL: You can force close existing trades here if you want extreme safety.
                # For now, we will just BLOCK NEW ENTRIES and let stops handle existing ones.
                pass
            else:
                 # If we are cash, STAY in SGOV.
                 # Only log occasionally
                if current_bar_index % 78 == 0: 
                    log(f"GOVERNOR: SPY Trend Negative ({spy_trend:.2%}). Holding SGOV.")
                return TargetAllocation({"SGOV": 1.0})

        # 3. VXX REGIME CHECK (The Shield)
        vix_data = self.get_history(d, self.vixy)
        is_defensive = False
        
        if len(vix_data) >= self.vix_ma_len:
            vix_closes = [x["close"] for x in vix_data[-self.vix_ma_len:]]
            vix_ma = sum(vix_closes) / len(vix_closes)
            current_vix = vix_data[-1]["close"]
            if current_vix > vix_ma:
                is_defensive = True

        # --- EXECUTE REGIME ---
        if is_defensive:
            # DEFENSIVE MODE
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
            # OFFENSIVE MODE
            scores = {}
            histories = {t: self.get_history(d, t) for t in self.tickers}
            
            for t in self.tickers:
                scores[t] = self.calculate_momentum(histories[t], self.mom_len)
            
            ranked_tickers = sorted(scores, key=scores.get, reverse=True)
            leader = ranked_tickers[0]
            follower = ranked_tickers[1] if len(ranked_tickers) > 1 else ranked_tickers[0]
            
            # ABSOLUTE MOMENTUM FILTER
            if scores[leader] < 0:
                if self.primary_asset is None:
                    return TargetAllocation({"SGOV": 1.0})
            
            # A. ENTER NEW TRADE
            if self.primary_asset is None:
                # COOLDOWN CHECK
                last_stop = self.last_stop_index.get(leader, -999)
                if current_bar_index - last_stop < self.cooldown_bars:
                    return TargetAllocation({"SGOV": 1.0})
                
                # EXECUTE ENTRY 
                if len(histories[leader]) > 0 and scores[leader] > 0:
                    self.primary_asset = leader
                    self.secondary_asset = follower
                    self.entry_price = histories[leader][-1]["close"]
                    self.peak_price = self.entry_price
                    self.rotation_stage = 0
                    log(f"ENTRY: {leader} at {self.entry_price} (Trend: {scores[leader]:.2%})")
                    return TargetAllocation({leader: 1.0})
                else:
                    return TargetAllocation({"SGOV": 1.0})

            # B. MANAGE EXISTING TRADE
            p_hist = histories.get(self.primary_asset, [])
            if p_hist:
                current_price = p_hist[-1]["close"]
                self.peak_price = max(self.peak_price, current_price)
                
                atr = self.calculate_atr(p_hist)
                if atr == 0: atr = current_price * 0.02
                
                profit_pct = (current_price / self.entry_price) - 1
                
                # STOP LOSS (4.0x ATR)
                if current_price <= self.entry_price - (4.0 * atr):
                    log(f"STOP: 4.0x ATR Hit on {self.primary_asset}. Cooling down in SGOV.")
                    self.last_stop_index[self.primary_asset] = current_bar_index
                    self.primary_asset = None
                    self.rotation_stage = 0
                    return TargetAllocation({"SGOV": 1.0}) 

                # ROTATION STAGES
                if self.rotation_stage == 0 and profit_pct >= 0.20:
                    self.rotation_stage = 1
                    self.secondary_asset = follower 
                    log(f"ROTATION: +20% Gain. Buying {self.secondary_asset}")
                    return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})

                elif self.rotation_stage == 1:
                    if current_price <= self.peak_price - (5.0 * atr):
                        self.rotation_stage = 2
                        log(f"ROTATION: Trail Hit. Loading {self.secondary_asset}")
                        return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})
                
                elif self.rotation_stage == 2:
                    if current_price <= self.entry_price:
                        self.primary_asset = self.secondary_asset
                        self.secondary_asset = ranked_tickers[2]
                        p_new_hist = self.get_history(d, self.primary_asset)
                        if p_new_hist:
                            self.entry_price = p_new_hist[-1]["close"]
                            self.peak_price = self.entry_price
                            self.rotation_stage = 0
                            return TargetAllocation({self.primary_asset: 1.0})

                if self.rotation_stage == 0:
                    return TargetAllocation({self.primary_asset: 1.0})
                elif self.rotation_stage == 1:
                    return TargetAllocation({self.primary_asset: 0.66, self.secondary_asset: 0.33})
                elif self.rotation_stage == 2:
                    return TargetAllocation({self.primary_asset: 0.33, self.secondary_asset: 0.66})

        return None