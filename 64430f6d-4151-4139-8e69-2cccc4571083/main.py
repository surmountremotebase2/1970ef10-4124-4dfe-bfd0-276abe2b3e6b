from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        self.tickers = ["TECL", "GDXU", "SOXL", "UPRO", "UCO", "AGQ"]
        self.vwap_len = 12
        self.rvol_threshold = 1.8 
        self.trailing_stop_pct = 0.08 
        self.take_profit_pct = 0.10 
        self.max_allocation = 0.50 
       
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
        recent_df = df.tail(12)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        sma_macro = df['close'].mean()
       
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
       
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            cp = current_bar["close"]
           
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp
           
            if self.entry_price and cp >= self.entry_price * (1 + self.take_profit_pct):
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})

            if cp <= self.peak_price * (1 - self.trailing_stop_pct):
                self.active_trade = False
                self.active_ticker = None
                return TargetAllocation({})
            return None

        scores = {}
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            score = self.get_conviction_score(hist)
            if score > 0: scores[t] = score
       
        if scores:
            best_ticker = max(scores, key=scores.get)
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            self.entry_price = d[-1][best_ticker]["close"]
            return TargetAllocation({best_ticker: self.max_allocation})

        return None