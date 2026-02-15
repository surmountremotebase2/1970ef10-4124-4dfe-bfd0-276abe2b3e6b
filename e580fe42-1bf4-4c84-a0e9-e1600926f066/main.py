from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE (SERIES K - IRON GRIP) ---
        # Goal: Stay in the trade through the noise. Minimize exits to maximize compounding.
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "URNM", "SILJ", "BITU"]
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390  
        self.mom_len = 40 # Keep entry fast (catch the move early)
        self.trend_len = 390 # Market Trend
        self.safe_len = 20     
        self.atr_period = 14
        
        # LOCKOUT: 1 Day (Required for compliance)
        self.system_lockout_counter = 0
        self.lockout_duration = 78 
        
        # --- STATE MACHINE ---
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
        
        # 1. DIAGNOSTIC LOG
        if not self.debug_printed:
            try:
                log(f"SERIES K (IRON GRIP): Stop=6.0x | Trail=8.0x. Holding on tight.")
            except:
                pass
            self.debug_printed = True

        # --- SYSTEM LOCKOUT CHECK ---
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            return TargetAllocation({"SGOV": 1.0})

        # 2. GLOBAL MARKET CHECK (Governor)
        spy_hist = self.get_history(d, self.spy)
        spy_trend = self.calculate_momentum(spy_hist, self.trend_len)
        
        if spy_trend < 0:
            if self.primary_asset is None:
                return TargetAllocation({"SGOV": 1.0})

        # 3. VXX REGIME CHECK (Shield)
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
            self.primary_asset = None
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
            scores = {}
            histories = {t: self.get_history(d, t) for t in self.tickers}
            
            for t in self.tickers:
                scores[t] = self.calculate_momentum(histories[t], self.mom_len)
            
            ranked_tickers = sorted(scores, key=scores.get, reverse=True)
            leader = ranked_tickers[0]
            
            # Absolute Momentum Filter
            if scores[leader] < 0:
                if self.primary_asset is None:
                    return TargetAllocation({"SGOV": 1.0})
            
            # A. ENTER NEW TRADE
            if self.primary_asset is None:
                if len(histories[leader]) > 0 and scores[leader] > 0:
                    self.primary_asset = leader
                    self.entry_price = histories[leader][-1]["close"]
                    self.peak_price = self.entry_price
                    log(f"ENTRY: {leader} at {self.entry_price}")
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
                
                # --- EXIT LOGIC (WIDENED FOR SURVIVAL) ---
                
                # 1. STOP LOSS (6.0x ATR) -> PREVENTING SHAKE OUT
                if current_price <= self.entry_price - (6.0 * atr):
                    log(f"STOP: 6.0x ATR Hit on {self.primary_asset}. Locking 1 Day.")
                    self.system_lockout_counter = self.lockout_duration
                    self.primary_asset = None
                    return TargetAllocation({"SGOV": 1.0}) 

                # 2. TRAILING STOP (8.0x ATR) -> LETTING WINNERS RUN
                if current_price <= self.peak_price - (8.0 * atr):
                     log(f"EXIT: Trailing Stop (8.0x) Hit on {self.primary_asset}. Banking & Locking.")
                     self.system_lockout_counter = self.lockout_duration
                     self.primary_asset = None
                     return TargetAllocation({"SGOV": 1.0})

                # 3. HOLD
                return TargetAllocation({self.primary_asset: 1.0})

        return None