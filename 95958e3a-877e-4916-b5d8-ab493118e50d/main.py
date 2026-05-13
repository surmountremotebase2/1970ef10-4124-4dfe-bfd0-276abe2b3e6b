from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # The Apex Roster: Tech, Semi, Gold, Silver, Oil, Bonds, and Crypto
        self.tickers = ["TECL", "SOXL", "AGQ", "UCO", "GDXU", "TMF", "BITX"]
        
        # Core Engine Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.take_profit_pct = 0.10 # The "Nitro" 10% Target
        self.trailing_stop_pct = 0.06 # Tightened to 6% to protect 100% allocation
        self.max_allocation = 1.0 # 100% Allocation (Ready for June 4th)
        
        # Internal State
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 78: return 0
        df = pd.DataFrame(history)
        
        # VWAP and Price Strength
        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        # RVOL (Volume Conviction)
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        # NEW: The Wick Filter (Must close in top 25% of the 5-min bar)
        candle_range = df['high'].iloc[-1] - df['low'].iloc[-1]
        if candle_range == 0: return 0
        close_relative = (current_price - df['low'].iloc[-1]) / candle_range
        
        # Entry logic: Above VWAP, Above SMA, High Volume, and Strong Close
        if current_price > vwap and current_price > df['close'].mean() and rvol >= self.rvol_threshold:
            if close_relative > 0.75: # Must be closing strong
                return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # 1. EXIT LOGIC
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            cp = current_bar["close"]
            
            # Update peak for trailing stop
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp
            
            # 10% Take Profit
            if cp >= self.entry_price * (1 + self.take_profit_pct):
                log(f"APEX PROFIT: {self.active_ticker} at {cp}. 10% Secured.")
                self._reset()
                return TargetAllocation({})

            # 6% Trailing Stop
            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                log(f"APEX STOP: {self.active_ticker} at {cp}. Peak: {self.peak_price}.")
                self._reset()
                return TargetAllocation({})
            
            return None

        # 2. ENTRY LOGIC
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
            self.entry_price = d[-1][best_ticker]["close"]
            
            log(f"APEX ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None

    def _reset(self):
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None