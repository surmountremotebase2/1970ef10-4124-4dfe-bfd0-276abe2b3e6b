from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        self.tickers = ["SOXL", "GDXU", "AGQ", "SPY", "VIXY"]
        
        # Engine Parameters
        self.vwap_len = 60 # Extended to 60 to reflect 1 hour of data on a 1-min chart
        self.spy_sma_len = 50
        self.rvol_threshold = 1.8
        self.max_allocation = 1.00 
        
        # Percentage-Based Risk Management
        self.take_profit_pct = 1.05 # Hard 5% take-profit target
        self.trailing_stop_pct = 0.015 # Tight 1.5% trailing stop
        
        # State Management
        self.active_trade = False
        self.active_ticker = None
        self.entry_price = None
        self.peak_price = None

    @property
    def interval(self): 
        return "1min"

    @property
    def assets(self): 
        return self.tickers

    def market_regime_check(self, data):
        d = data.get("ohlcv")
        if not d: return "FLAT"
        
        spy_hist = [bar["SPY"] for bar in d if "SPY" in bar]
        vixy_hist = [bar["VIXY"] for bar in d if "VIXY" in bar]
        
        if len(spy_hist) < self.spy_sma_len or len(vixy_hist) < 20: 
            return "FLAT"
            
        spy_df = pd.DataFrame(spy_hist)
        vixy_df = pd.DataFrame(vixy_hist)
        
        spy_sma = spy_df['close'].rolling(self.spy_sma_len).mean().iloc[-1]
        spy_current = spy_df['close'].iloc[-1]
        
        vixy_sma = vixy_df['close'].rolling(20).mean().iloc[-1]
        vixy_current = vixy_df['close'].iloc[-1]
        
        if spy_current > spy_sma and vixy_current < vixy_sma:
            return "RISK_ON"
        elif spy_current <= spy_sma or vixy_current >= vixy_sma:
            return "RISK_OFF"
            
        return "FLAT"

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None

        try:
            current_time = pd.to_datetime(d[-1][self.tickers[0]]['date'])
            hour = current_time.hour
            minute = current_time.minute
        except KeyError:
            hour = 10
            minute = 0
            
        is_eod = (hour == 15 and minute >= 50) # Liquidate 10 minutes before close
        is_midday_chop = (hour == 11 and minute >= 30) or (hour == 12) or (hour == 13)

        # --- 1. INTRADAY MANAGEMENT (Percentage Stops & EOD) ---
        if self.active_trade:
            current_bar = d[-1].get(self.active_ticker)
            if not current_bar: return None
            
            cp = current_bar["close"]
            
            if self.peak_price is None or cp > self.peak_price:
                self.peak_price = cp

            # EOD LIQUIDATION
            if is_eod:
                log(f"EOD LIQUIDATION: {self.active_ticker}. Flattening book.")
                self.active_trade = False
                self.active_ticker = None
                self.entry_price = None
                return TargetAllocation({})

            # TAKE PROFIT (5%)
            if cp >= self.entry_price * self.take_profit_pct:
                log(f"TAKE PROFIT HIT: {self.active_ticker} exited at {cp} (+5%).")
                self.active_trade = False
                self.active_ticker = None
                self.entry_price = None
                return TargetAllocation({})
            
            # TRAILING STOP (1.5% from peak)
            stop_loss_price = self.peak_price * (1 - self.trailing_stop_pct)
            if cp <= stop_loss_price:
                log(f"TRAILING STOP: {self.active_ticker} exited at {cp}.")
                self.active_trade = False
                self.active_ticker = None
                self.entry_price = None
                return TargetAllocation({}) 
            
            return None

        # --- 2. THE MACRO ROTATION FILTER ---
        if is_eod or is_midday_chop:
            return None

        regime = self.market_regime_check(data)
        
        if regime == "FLAT":
            return None
            
        allowed_tickers = []
        if regime == "RISK_ON":
            allowed_tickers = ["SOXL"] 
        elif regime == "RISK_OFF":
            allowed_tickers = ["GDXU", "AGQ"] 

        # --- 3. EXECUTION ---
        scores = {}
        for t in allowed_tickers:
            hist = [bar[t] for bar in d if t in bar]
            if len(hist) < 60: continue # Need 60 minutes for VWAP calculation
            
            df_t = pd.DataFrame(hist)
            vwap = (df_t['close'].tail(self.vwap_len) * df_t['volume'].tail(self.vwap_len)).sum() / df_t['volume'].tail(self.vwap_len).sum()
            cp = df_t['close'].iloc[-1]
            
            avg_vol = df_t['volume'].tail(20).mean()
            rvol = df_t['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
            
            if cp > vwap and rvol >= self.rvol_threshold:
                scores[t] = rvol
        
        if scores:
            best_ticker = max(scores, key=scores.get)
            
            self.active_ticker = best_ticker
            self.active_trade = True
            self.entry_price = d[-1][best_ticker]["close"]
            self.peak_price = self.entry_price
            
            log(f"ENTRY: {best_ticker} | Regime: {regime} | Price: {self.entry_price}")
            return TargetAllocation({best_ticker: self.max_allocation})

        return None