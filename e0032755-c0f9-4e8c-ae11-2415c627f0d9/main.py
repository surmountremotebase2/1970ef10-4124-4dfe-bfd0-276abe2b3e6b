from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # 3x Bull Basket for Rotation
        self.tickers = ["TECL", "DFEN", "FAS"]
        
        self.vwap_len = 12 
        self.rvol_threshold = 1.8 
        self.trailing_stop_pct = 0.045 
        self.max_allocation = 0.50 # CASH ACCOUNT SHIELD
        
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # 1. THE 3:50 PM KILL SWITCH (Daily Liquidity)
        ref_bar = d[-1].get(self.tickers[0])
        if ref_bar and "15:50" in ref_bar.get("time", ""):
            if self.active_trade:
                log(f"EOD SHIELD: Closing {self.active_ticker} for T+1 settlement.")
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})
            return None

        # 2. MANAGEMENT (The 4.5% Volatility Buffer)
        if self.active_trade:
            cp = d[-1][self.active_ticker]["close"]
            self.peak_price = max(self.peak_price, cp)
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"BUFFER EXIT: {self.active_ticker} stopped at {cp}. Peak: {self.peak_price}")
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})
            return None

        # 3. LEADERSHIP SCAN (Relative Volume Ranking)
        best_rvol = 0
        target = None
        
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            if len(hist) < 78: continue # Ensure we have at least 1 day of data
            
            df = pd.DataFrame(hist)
            # Volume-Weighted Average Price
            vwap = (df['close'].tail(12) * df['volume'].tail(12)).sum() / df['volume'].tail(12).sum()
            # Relative Volume
            rvol = df['volume'].iloc[-1] / df['volume'].tail(20).mean()
            # Trend Check (Above 1-day SMA)
            is_trending = df['close'].iloc[-1] > df['close'].tail(78).mean()
            
            if is_trending and df['close'].iloc[-1] > vwap and rvol > self.rvol_threshold:
                if rvol > best_rvol:
                    best_rvol = rvol
                    target = t

        if target:
            self.active_ticker = target
            self.active_trade = True
            self.peak_price = d[-1][target]["close"]
            log(f"ROTATION: Buying {target} with Tranche 1 (50% Load). RVOL: {best_rvol:.2f}")
            return TargetAllocation({target: self.max_allocation})

        return None