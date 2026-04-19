from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Optimized Intraday Roster - Seed Money
        self.tickers = ["GDXU", "SOXL", "UCO"]
        
        # Core Engine Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.max_allocation = 1.00 # 100% All-or-nothing execution
        
        # Dynamic Risk Parameters
        self.atr_multiplier = 2.0 # Trailing stop will be 2x the 5-min ATR
        
        self.active_trade = False
        self.active_ticker = None
        self.peak_price = None
        self.entry_price = None
        self.current_atr = None

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_atr(self, df, period=14):
        # Calculates the Average True Range to measure real-time asset volatility
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean().iloc[-1]

    def get_conviction_score(self, history):
        if len(history) < 78: return 0
        df = pd.DataFrame(history)
        
        # VWAP calculation
        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        # RVOL calculation
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        # Macro Trend Check
        sma_macro = df['close'].mean()
        
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None

        # Surmount timestamps are typically passed in the data object. 
        # We extract the hour and minute to enforce Time-of-Day rules.
        # Assuming standard EST market hours for the backtest environment.
        try:
            # Platform dependent: adjust timestamp extraction if Surmount updates their payload
            current_time = pd.to_datetime(d[-1][self.tickers[0]]['date'])
            hour = current_time.hour
            minute = current_time.minute
        except KeyError:
            # Fallback if timestamp mapping is missing in a specific Surmount testing block
            hour = 10
            minute = 0
            
        is_eod = (hour == 15 and minute >= 55)
        is_midday_chop = (hour == 11 and minute >= 30) or (hour == 12) or (hour == 13)

        # --- 1. INTRADAY MANAGEMENT (ATR Trailing Stop & EOD Liquidation) ---
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            
            cp = current_bar["close"]
            
            # Continuously update the peak price
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp

            # FLAT RULE: Liquidate at 3:55 PM EST to avoid overnight gaps
            if is_eod:
                log(f"EOD LIQUIDATION: {self.active_ticker} exit at {cp}. Flattening book.")
                self.active_trade = False
                self.active_ticker = None
                self.peak_price = None
                self.entry_price = None
                self.current_atr = None
                return TargetAllocation({})

            # DEFENSIVE EXIT: Dynamic ATR Trailing Stop
            if self.current_atr:
                stop_loss_price = self.peak_price - (self.current_atr * self.atr_multiplier)
                if cp <= stop_loss_price:
                    log(f"ATR STOP: {self.active_ticker} exit at {cp}. Peak was {self.peak_price}.")
                    self.active_trade = False
                    self.active_ticker = None
                    self.peak_price = None
                    self.entry_price = None
                    self.current_atr = None
                    return TargetAllocation({}) 
            
            return None

        # --- 2. PREDATORY SELECTION (Time-of-Day Filtered) ---
        
        # Block new entries during EOD or midday chop hours
        if is_eod or is_midday_chop:
            return None

        scores = {}
        for t in self.tickers:
            hist = [bar[t] for bar in d if t in bar]
            score = self.get_conviction_score(hist)
            if score > 0:
                scores[t] = score
        
        if scores:
            best_ticker = max(scores, key=scores.get)
            hist = [bar[best_ticker] for bar in d if best_ticker in bar]
            df_best = pd.DataFrame(hist)
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.peak_price = d[-1][best_ticker]["close"]
            self.entry_price = d[-1][best_ticker]["close"]
            
            # Calculate and lock in the real-time ATR for this specific trade's stop-loss
            self.current_atr = self.get_atr(df_best)
            
            log(f"INTRADAY ENTRY: {best_ticker} | RVOL: {scores[best_ticker]:.2f} | Entry: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None