from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import pandas_ta as ta

class TradingStrategy(Strategy):
    def __init__(self):
        # Tradable universe (all 3x leveraged)
        self.tickers = ["SOXL", "TECL", "AGQ", "UCO", "GDXU"]
        self.max_positions = 2
        self.take_profit = 0.10
        self.trailing_stop = 0.08
        
        # State tracking
        self.entry_prices = {}
        self.high_water_marks = {}

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
        
        # Gatekeeper: Only flips to True on explicit entry or exit
        state_changed = False 
        
        active_tickers = [ticker for ticker in holdings if holdings[ticker] > 0]
        
        # Phase 1: Managed Active EXITS (Structural Rebuild using H/L context)
        for ticker in active_tickers:
            
            # We must pull the full OHLCV context, not just the Close data.
            full_data = [row[ticker] for row in data["ohlcv"] if ticker in row]
            if not full_data:
                continue
            
            # Current 5-min context
            current_low = full_data[-1]["low"] # Used for Exit checks (Worst case)
            current_high = full_data[-1]["high"] # Used for Peak checks (Best case)
            
            entry_px = self.entry_prices.get(ticker)
            
            # 1a. Lock the Peak (Update High Water Mark using High of bar)
            if ticker not in self.high_water_marks:
                self.high_water_marks[ticker] = current_high
            self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_high)
            
            # Establish the mathematical peak and the trailing stop trigger
            highest_price_achieved = self.high_water_marks[ticker]
            trailing_stop_trigger = highest_price_achieved * (1.0 - self.trailing_stop)
            
            # 1b. Exit Logic (Evaluated on Low of bar to capture whipsaws)
            if entry_px is not None:
                take_profit_trigger = entry_px * (1.0 + self.take_profit)
                # Check Take Profit Exit
                if current_low >= take_profit_trigger:
                    allocations[ticker] = 0.0 
                    self.entry_prices.pop(ticker, None)
                    self.high_water_marks.pop(ticker, None)
                    log(f"TAKE PROFIT (Intra-Bar H/L): {ticker} exit Low was {current_low}")
                    state_changed = True
                    continue

            # Check Trailing Stop Exit
            # If the LOW wick dipped below the trailing stop trigger, the broker executed the sell order.
            if current_low <= trailing_stop_trigger:
                allocations[ticker] = 0.0 
                self.entry_prices.pop(ticker, None)
                self.high_water_marks.pop(ticker, None)
                log(f"SWING STOP (Intra-Bar H/L): {ticker} exit Low was {current_low}")
                state_changed = True
                continue

            # 1c. Maintain State (If not exited, freeze position for dynamic remainder calculation)
            allocations[ticker] = 0.50
            frozen_weight += 0.50

        # Phase 2: Predatory Selection (ENTRIES)
        if len(active_tickers) < self.max_positions:
            candidates = {}
            
            for ticker in self.tickers:
                if ticker in active_tickers:
                    continue
                    
                # Ingest data context for entry triggers
                ticker_hist = [row[ticker] for row in data["ohlcv"] if ticker in row]
                df = pd.DataFrame(ticker_hist)
                
                # Memory Shield: Sliding window check for the native 200 SMA
                if len(df) < 200: 
                    continue
                
                df['date'] = pd.to_datetime(df['date'])
                last_timestamp = df['date'].iloc[-1]
                
                # Time Shield: Block the engine from scanning entries between 9:30 AM and 9:55 AM
                if last_timestamp.hour == 9:
                    continue
                
                current_price = df['close'].iloc[-1]
                
                # Trigger 1: Intraday Momentum (12-period VWMA)
                df['vwma_12'] = ta.vwma(df['close'], df['volume'], length=12)
                vwap_bullish = current_price > df['vwma_12'].iloc[-1]
                
                # Trigger 2: Asset Momentum (MACD - bullish postural crossing)
                macd = ta.macd(df['close'])
                if macd is not None and not macd.empty:
                    macd_bullish = macd['MACD_12_26_9'].iloc[-1] > macd['MACDs_12_26_9'].iloc[-1]
                else:
                    macd_bullish = False
                
                # Trigger 3: Predatory Volume (RVOL >= 1.8x over 20 periods)
                df['vol_sma_20'] = ta.sma(df['volume'], length=20)
                rvol = df['volume'].iloc[-1] / df['vol_sma_20'].iloc[-1]
                
                # Trigger 4: Structural Filter (Native 200-period SMA on 5min chart)
                df['sma_200'] = ta.sma(df['close'], length=200)
                macro_safe = current_price > df['sma_200'].iloc[-1]

                # Composite Execution Filter
                if vwap_bullish and macro_safe and rvol >= 1.8 and macd_bullish:
                    candidates[ticker] = rvol

            # Phase 3: Dynamic Remainder Execution
            if candidates:
                # Rank candidates by strongest current volume conviction
                sorted_candidates = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
                
                for ticker, rvol_score in sorted_candidates:
                    # Margin verification
                    if len([k for k, v in allocations.items() if v > 0]) < self.max_positions:
                        
                        # Mathematical remainder logic (Sum must not exceed 1.0)
                        remaining_weight_available = 1.0 - frozen_weight
                        target_weight = min(0.50, remaining_weight_available)
                        
                        if target_weight > 0:
                            allocations[ticker] = target_weight
                            frozen_weight += target_weight
                            
                            # Log entry for internal state tracking
                            entry_px = df['close'].iloc[-1]
                            self.entry_prices[ticker] = entry_px
                            
                            # Initialize the HWM to the high of the same bar we bought
                            self.high_water_marks[ticker] = df['high'].iloc[-1]
                            
                            log(f"SWING ENTRY ({int(target_weight*100)}%): {ticker} | RVOL: {rvol_score:.2f}")
                            state_changed = True

        # Phase 4: Final output sync
        if state_changed:
            return TargetAllocation(allocations)
            
        return None