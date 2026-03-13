from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # THE 2026 ALL-WEATHER BASKET
        self.tickers = ["TECL", "DFEN", "TNA", "GDXU"]
        
        # PARTNER-TUNED PARAMETERS
        self.vwap_len = 12 
        self.rvol_threshold = 1.8 
        self.trailing_stop_pct = 0.045 
        self.max_allocation = 0.50 
        
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 20: return 0
        df = pd.DataFrame(history)
        
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        sma_day = df['close'].tail(78).mean()
        
        if current_price > vwap and current_price > sma_day and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # --- 1. EOD LIQUIDITY SHIELD (3:50 PM EST HARD EXIT) ---
        ref_bar = d[-1].get(self.tickers[0])
        if ref_bar and "time" in ref_bar:
            try:
                # Parse timestamp, assume UTC (Surmount default), convert to Eastern Time
                dt = pd.to_datetime(ref_bar["time"])
                if dt.tzinfo is None:
                    dt = dt.tz_localize('UTC')
                ny_time = dt.tz_convert('America/New_York')
                
                # Check if it is 3:50 PM EST or later
                if ny_time.hour == 15 and ny_time.minute >= 50:
                    if self.active_trade:
                        log(f"EOD EXIT: Closing {self.active_ticker} at {ny_time.strftime('%H:%M')} EST for T+1 settlement.")
                        self.active_trade = False
                        self.active_ticker = None
                    return TargetAllocation({})
            except Exception as e:
                log(f"Time parsing error: {e}")

        # --- 2. ACTIVE MANAGEMENT (4.5% Buffer) ---
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            
            cp = current_bar["close"]
            self.peak_price = max(self.peak_price, cp)
            
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"STOP TRIGGERED: {self.active_ticker} closed. Protecting capital.")
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})
            return None

        # --- 3. THE BEAUTY CONTEST (Selection Logic) ---
        scores = {}
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            score = self.get_conviction_score(hist)
            if score > 0:
                scores[t] = score
        
        if scores:
            best_ticker = max(scores, key=scores.get)
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            
            log(f"PREDATORY ENTRY: {best_ticker} | Score: {scores[best_ticker]:.2f}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None