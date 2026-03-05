from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        self.tickers = ["TECL", "DFEN", "FAS"]
        
        # Partner Adjustments
        self.vwap_len = 12 
        self.rvol_threshold = 1.8 # Increased for higher conviction
        self.trailing_stop_pct = 0.045 # Widened to 4.5% to avoid noise
        self.max_allocation = 0.50 # CASH ACCOUNT SHIELD: Only use 50% at a time
        
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
        
        # 1. EOD SHIELD (3:50 PM EST)
        ref_bar = d[-1].get(self.tickers[0])
        if ref_bar and "15:50" in ref_bar.get("time", ""):
            if self.active_trade:
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})
            return None

        # 2. MANAGEMENT (4.5% Trailing Stop)
        if self.active_trade:
            cp = d[-1][self.active_ticker]["close"]
            self.peak_price = max(self.peak_price, cp)
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"PARTNER EXIT: {self.active_ticker} Stop Hit. Protecting Capital.")
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})
            return None

        # 3. ROTATION SCAN (RVOL & VWAP)
        best_rvol = 0
        target = None
        
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            if len(hist) < 20: continue
            
            df = pd.DataFrame(hist)
            vwap = (df['close'].tail(12) * df['volume'].tail(12)).sum() / df['volume'].tail(12).sum()
            rvol = df['volume'].iloc[-1] / df['volume'].tail(20).mean()
            
            if df['close'].iloc[-1] > vwap and rvol > self.rvol_threshold:
                if rvol > best_rvol:
                    best_rvol = rvol
                    target = t

        if target:
            self.active_ticker = target
            self.active_trade = True
            self.peak_price = d[-1][target]["close"]
            log(f"PARTNER ENTRY: {target} | Allocation: 50% (T+1 Shield Active)")
            return TargetAllocation({target: self.max_allocation})

        return None