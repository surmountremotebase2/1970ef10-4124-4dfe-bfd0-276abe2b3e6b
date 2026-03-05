# Imports matching the core platform requirements shown in your image
from surmount.base_class import Strategy, TargetAllocation, backtest
from surmount.logging import log
# Standard additions needed for indicator logic and time management
import pandas as pd
import numpy as np
from datetime import time

class TradingStrategy(Strategy):
    def __init__(self):
        # --- CLASS VARIABLES ---
        self.ticker = "TQQQ"
        self.tickers = [self.ticker] # For the assets property
        
        # --- STRATEGY PARAMS ---
        # The defensive barrier. 2.5% intraday drop in TQQQ ETFs triggers immediate sell.
        self.trailing_stop_pct = 0.025 
        # A simple daily constraint to avoid over-trading in choppy markets.
        self.max_trades_per_day = 1 
        
        # --- DAILY STATE VARIABLES ---
        # NOTE: These MUST be managed inside the run() loop to reset daily.
        self.has_position = False
        self.high_water_mark = 0.0
        self.trades_today = 0
        self.last_reset_day = None

    @property
    def interval(self):
        # NOTE: User's image used "1day", but this IS CRITICAL:
        # We must use "1min" intervals to execute our INTRADAY strategy.
        return "1min"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        # --- 1. DATA GATHERING & DAILY RESET ---
        try:
            # Safely access current minute bar data for TQQQ
            current_bar = data[self.ticker][-1]
            historical_bars = data[self.ticker] # The full list of minutes provided so far
        except (KeyError, IndexError):
            return None # Skip this minute if data is missing

        # Extract current price and time (Assume datetime object in EST)
        current_price = current_bar['close']
        current_time_est = current_bar['time']
        
        # Determine if a new day has started to reset daily state variables
        current_day = current_time_est.date()
        if self.last_reset_day != current_day:
            self.last_reset_day = current_day
            self.has_position = False
            self.high_water_mark = 0.0
            self.trades_today = 0
            log(f"--- Daily State Reset for TQQQ Strategy on {current_day} ---")
            return None # Skip further logic on the first minute of the day to ensure clean data

        # --- SECTION 1: EOD FLATTEN (The T+1 Enabler) ---
        # Hard exit at 3:50 PM EST. We do not hold overnight under ANY circumstance.
        if current_time_est.time() >= time(15, 50):
            if self.has_position:
                log(f"EOD Flatten Triggered. Liquidating TQQQ at {current_price}")
                self.has_position = False # Reset state internally
                # Send empty dict to TargetAllocation to close all positions.
                return TargetAllocation({}) 
            return None # Position is already flat, or we did nothing today

        # --- SECTION 2: DEFENSE (TRAILING STOP LOSS) ---
        # This logic is only active when we are managing an open position.
        if self.has_position:
            # 2a. Update high water mark if current price is a new peak for this trade
            if current_price > self.high_water_mark:
                self.high_water_mark = current_price
                # log(f"New High Mark: {self.high_water_mark}")
            
            # 2b. Check stop condition (2.5% drop from peak high water mark)
            stop_price = self.high_water_mark * (1 - self.trailing_stop_pct)
            if current_price <= stop_price:
                log(f"--- TRAILING STOP TRIGGERED --- Sold TQQQ at {current_price} (Peak was {self.high_water_mark})")
                self.has_position = False # Crucial state reset
                return TargetAllocation({}) # Immediate liquidation order
            
            return None # Position is safe, nothing to execute this minute

        # --- SECTION 3: OFFENSE (ENTRY TRIGGER) ---
        # We only hunt for setups in the first 60 minutes of the open (9:30 - 10:30 AM EST).
        entry_window_start = time(9, 30)
        entry_window_end = time(10, 30)
        current_time_only = current_time_est.time()
        
        # Valid Entry Check: Flat, right time, under trade limit
        if not self.has_position and \
           entry_window_start <= current_time_only <= entry_window_end and \
           self.trades_today < self.max_trades_per_day:

            # 3a. VWAP Calculation (using pandas for robust data handling)
            df = pd.DataFrame(historical_bars)
            
            # 5-minute VWAP simplified logic:
            # (Last 5 Close Prices * Last 5 Volumes).sum() / (Last 5 Volumes).sum()
            # If historical bars contain less than 5 data points, skip.
            if len(df) >= 5:
                recent_df = df.tail(5)
                # Ensure data types are floats/ints for calculation
                prices = recent_df['close'].astype(float)
                volumes = recent_df['volume'].astype(float)
                # Perform the weighted calculation
                vwap_5m = (prices * volumes).sum() / volumes.sum()
            else:
                return None # Not enough data for VWAP calculation

            # 3b. Final Buy Condition: Price crosses above the VWAP filter.
            # *Simplified implementation: RVOL filter omitted initially for robust backtesting.*
            if current_price > vwap_5m:
                # Execute BUY: Send order allocation percentage
                # We want ~33.3% weight to rotate capital tranche (Matching image weight syntax style)
                # log(f"Buy Signal Confirmed: Entered TQQQ at {current_price}. (Weight: 33.3%)")
                self.has_position = True
                self.high_water_mark = current_price # Set initial high mark for trailing stop
                self.trades_today += 1 # Increment daily limit
                
                # Matching user's image syntax for TargetAllocation weight allocation
                allocation_dict = {
                    self.ticker: 33.3 # A 'weight' of 33.3 out of a possible 100
                }
                return TargetAllocation(allocation_dict)

        return None # Default: Do nothing this minute