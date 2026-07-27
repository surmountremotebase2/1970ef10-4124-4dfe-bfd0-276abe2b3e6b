from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import pandas_ta as ta
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Tradable universe (all 2x/3x leveraged)
        self.tickers = ["SOXL", "TECL", "AGQ", "UCO", "GDXU"]
        
        # Core parameters (easy to tune)
        self.max_positions = 2
        self.take_profit = 0.10 # 10% take profit
        self.atr_multiplier = 2.8 # ATR trailing stop multiplier
        self.min_hold_bars = 6 # \~30 minutes minimum hold
        self.max_hold_bars = 300 # \~4 trading days hard maximum hold
        self.rvol_threshold = 2.2 # raised from 2.0
        self.max_extension = 0.018 # max 1.8% above VWMA
        
        # State tracking
        self.entry_prices = {}
        self.high_water_marks = {}
        self.entry_bar_count = {}

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        holdings = data["holdings"]
        allocations = {}
        frozen_weight = 0.0
        state_changed = False
        
        active_tickers = [t for t in holdings if holdings.get(t, 0) > 0]
        
        # -------------------------------------------------
        # Phase 1: Manage existing positions (EXITS)
        # -------------------------------------------------
        for ticker in active_tickers:
            full_data = [row[ticker] for row in data["ohlcv"] if ticker in row]
            if not full_data or len(full_data) < 30: # need enough bars for ATR
                continue
            
            df = pd.DataFrame(full_data)
            current_bar = full_data[-1]
            current_low = current_bar["low"]
            current_high = current_bar["high"]
            current_close = current_bar["close"]
            
            # Increment hold counter
            self.entry_bar_count[ticker] = self.entry_bar_count.get(ticker, 0) + 1
            bars_held = self.entry_bar_count[ticker]
            
            # Reconstruct entry price if state was lost
            if ticker not in self.entry_prices:
                self.entry_prices[ticker] = current_close
                self.high_water_marks[ticker] = current_high
                log(f"STATE RECONSTRUCT: {ticker} entry approximated to {current_close:.2f}")
            
            entry_px = self.entry_prices[ticker]
            
            # Update high-water mark
            self.high_water_marks[ticker] = max(
                self.high_water_marks.get(ticker, current_high), 
                current_high
            )
            highest = self.high_water_marks[ticker]
            
            # ATR-based trailing stop
            atr = ta.atr(df['high'], df['low'], df['close'], length=14)
            if atr is None or pd.isna(atr.iloc[-1]):
                # Fallback to fixed % if ATR unavailable
                trailing_stop_trigger = highest * (1.0 - 0.10)
            else:
                trail_distance = atr.iloc[-1] * self.atr_multiplier
                trailing_stop_trigger = highest - trail_distance
            
            take_profit_trigger = entry_px * (1.0 + self.take_profit)
            
            # ----- EXIT LOGIC -----
            exit_reason = None
            
            # 1. Take Profit (use HIGH of bar)
            if current_high >= take_profit_trigger:
                exit_reason = "TAKE PROFIT"
            
            # 2. ATR Trailing Stop (use LOW of bar)
            elif current_low <= trailing_stop_trigger:
                exit_reason = "ATR TRAILING STOP"
            
            # 3. Hard maximum hold time
            elif bars_held >= self.max_hold_bars:
                exit_reason = "MAX HOLD TIME"
            
            # Minimum hold protection (only for trailing stop)
            if exit_reason == "ATR TRAILING STOP" and bars_held < self.min_hold_bars:
                exit_reason = None
            
            if exit_reason:
                allocations[ticker] = 0.0
                self.entry_prices.pop(ticker, None)
                self.high_water_marks.pop(ticker, None)
                self.entry_bar_count.pop(ticker, None)
                log(f"{exit_reason}: {ticker} | Entry={entry_px:.2f} High={highest:.2f} Low={current_low:.2f} Bars={bars_held}")
                state_changed = True
                continue
            
            # Hold current weight – no forced rebalancing
            current_weight = holdings.get(ticker, 0)
            allocations[ticker] = current_weight
            frozen_weight += current_weight
        
        # -------------------------------------------------
        # Phase 2: Look for new entries
        # -------------------------------------------------
        current_position_count = len([t for t in allocations if allocations.get(t, 0) > 0])
        open_slots = self.max_positions - current_position_count
        
        if open_slots > 0:
            candidates = {}
            
            for ticker in self.tickers:
                if ticker in active_tickers or ticker in allocations:
                    continue
                
                ticker_hist = [row[ticker] for row in data["ohlcv"] if ticker in row]
                if len(ticker_hist) < 210:
                    continue
                
                df = pd.DataFrame(ticker_hist)
                df['date'] = pd.to_datetime(df['date'])
                last_ts = df['date'].iloc[-1]
                
                # Time shield: block until 9:55
                if last_ts.hour == 9 and last_ts.minute < 55:
                    continue
                
                current_price = df['close'].iloc[-1]
                current_high = df['high'].iloc[-1]
                
                # Indicators with NaN protection
                vwma = ta.vwma(df['close'], df['volume'], length=12)
                if vwma is None or pd.isna(vwma.iloc[-1]):
                    continue
                vwma_val = vwma.iloc[-1]
                vwap_bullish = current_price > vwma_val
                extension = (current_price - vwma_val) / vwma_val
                
                macd = ta.macd(df['close'])
                if macd is None or macd.empty or pd.isna(macd['MACD_12_26_9'].iloc[-1]):
                    continue
                macd_bullish = macd['MACD_12_26_9'].iloc[-1] > macd['MACDs_12_26_9'].iloc[-1]
                
                vol_sma = ta.sma(df['volume'], length=20)
                if vol_sma is None or pd.isna(vol_sma.iloc[-1]) or vol_sma.iloc[-1] == 0:
                    continue
                rvol = df['volume'].iloc[-1] / vol_sma.iloc[-1]
                
                sma200 = ta.sma(df['close'], length=200)
                if sma200 is None or pd.isna(sma200.iloc[-1]):
                    continue
                macro_safe = current_price > sma200.iloc[-1]
                
                if (vwap_bullish and 
                    macro_safe and 
                    rvol >= self.rvol_threshold and 
                    macd_bullish and
                    extension <= self.max_extension):
                    
                    candidates[ticker] = {
                        "rvol": rvol,
                        "close": current_price,
                        "high": current_high
                    }
            
            if candidates:
                sorted_cands = sorted(candidates.items(), key=lambda x: x[1]["rvol"], reverse=True)
                
                for ticker, info in sorted_cands:
                    if open_slots <= 0:
                        break
                    
                    remaining = 1.0 - frozen_weight
                    target_weight = min(0.50, remaining)
                    
                    if target_weight <= 0.01:
                        break
                    
                    allocations[ticker] = target_weight
                    frozen_weight += target_weight
                    open_slots -= 1
                    
                    self.entry_prices[ticker] = info["close"]
                    self.high_water_marks[ticker] = info["high"]
                    self.entry_bar_count[ticker] = 0
                    
                    log(f"ENTRY ({int(target_weight*100)}%): {ticker} | RVOL={info['rvol']:.2f} | Px={info['close']:.2f}")
                    state_changed = True
        
        # -------------------------------------------------
        # Phase 3: Only return allocation when something changed
        # -------------------------------------------------
        if state_changed:
            total = sum(allocations.values())
            if total > 1.0:
                scale = 1.0 / total
                allocations = {k: v * scale for k, v in allocations.items()}
            return TargetAllocation(allocations)
        
        return None