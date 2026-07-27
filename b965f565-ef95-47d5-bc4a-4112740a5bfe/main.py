from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import pandas_ta as ta
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Tradable universe (all 3x leveraged)
        self.tickers = ["SOXL", "TECL", "AGQ", "UCO", "GDXU"]
        
        # Core parameters (easy to tune)
        self.max_positions = 2
        self.take_profit = 0.10 # 10%
        self.trailing_stop = 0.10 # widened from 0.08 for 3x volatility
        self.min_hold_bars = 6 # \~30 minutes minimum hold
        self.rvol_threshold = 2.0 # slightly stricter
        self.max_extension = 0.018 # don't buy if >1.8% above VWMA
        
        # State tracking
        self.entry_prices = {}
        self.high_water_marks = {}
        self.entry_bar_count = {} # tracks bars held for min-hold logic

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
            if not full_data or len(full_data) < 2:
                continue
            
            current_bar = full_data[-1]
            current_low = current_bar["low"]
            current_high = current_bar["high"]
            current_close = current_bar["close"]
            
            # Increment hold counter
            self.entry_bar_count[ticker] = self.entry_bar_count.get(ticker, 0) + 1
            
            # Reconstruct entry price if state was lost
            if ticker not in self.entry_prices:
                # Fallback: approximate from recent bars where we likely entered
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
            trailing_stop_trigger = highest * (1.0 - self.trailing_stop)
            take_profit_trigger = entry_px * (1.0 + self.take_profit)
            
            bars_held = self.entry_bar_count.get(ticker, 0)
            
            # ----- EXIT LOGIC -----
            exit_reason = None
            
            # Take Profit (use HIGH of bar - price was reached)
            if current_high >= take_profit_trigger:
                exit_reason = "TAKE PROFIT"
            
            # Trailing Stop (use LOW of bar - worst case)
            elif current_low <= trailing_stop_trigger:
                exit_reason = "TRAILING STOP"
            
            # Minimum hold protection (skip stop checks until min bars held)
            if exit_reason and bars_held < self.min_hold_bars and exit_reason == "TRAILING STOP":
                exit_reason = None # ignore early trailing stops
            
            if exit_reason:
                allocations[ticker] = 0.0
                self.entry_prices.pop(ticker, None)
                self.high_water_marks.pop(ticker, None)
                self.entry_bar_count.pop(ticker, None)
                log(f"{exit_reason}: {ticker} | Entry={entry_px:.2f} High={highest:.2f} Low={current_low:.2f}")
                state_changed = True
                continue
            
            # Maintain position (with mild drift tolerance)
            current_weight = holdings.get(ticker, 0)
            target = 0.50
            if abs(current_weight - target) > 0.08:
                allocations[ticker] = target
                state_changed = True
            else:
                allocations[ticker] = current_weight # leave alone
            
            frozen_weight += allocations[ticker]
        
        # -------------------------------------------------
        # Phase 2: Look for new entries
        # -------------------------------------------------
        open_slots = self.max_positions - len([t for t in allocations if allocations.get(t, 0) > 0])
        
        if open_slots > 0:
            candidates = {}
            
            for ticker in self.tickers:
                if ticker in active_tickers or ticker in allocations:
                    continue
                
                ticker_hist = [row[ticker] for row in data["ohlcv"] if ticker in row]
                if len(ticker_hist) < 210: # need room for 200 SMA + buffer
                    continue
                
                df = pd.DataFrame(ticker_hist)
                df['date'] = pd.to_datetime(df['date'])
                last_ts = df['date'].iloc[-1]
                
                # Time shield: block first \~25 min of regular session
                if last_ts.hour == 9 and last_ts.minute < 55:
                    continue
                
                current_price = df['close'].iloc[-1]
                current_high = df['high'].iloc[-1]
                
                # --- Indicators with NaN protection ---
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
                
                # Composite filter
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
            
            # Rank by relative volume and fill available slots
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
                    
                    # Record entry state
                    self.entry_prices[ticker] = info["close"]
                    self.high_water_marks[ticker] = info["high"]
                    self.entry_bar_count[ticker] = 0
                    
                    log(f"ENTRY ({int(target_weight*100)}%): {ticker} | RVOL={info['rvol']:.2f} | Px={info['close']:.2f}")
                    state_changed = True
        
        # -------------------------------------------------
        # Phase 3: Return allocation only if something changed
        # -------------------------------------------------
        if state_changed:
            # Ensure we never exceed 1.0
            total = sum(allocations.values())
            if total > 1.0:
                scale = 1.0 / total
                allocations = {k: v * scale for k, v in allocations.items()}
            
            return TargetAllocation(allocations)
        
        return None