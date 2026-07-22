from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import pandas_ta as ta

class TradingStrategy(Strategy):
    def __init__(self):
        # Tradable universe
        self.tickers = ["SOXL", "TECL", "AGQ", "UCO", "GDXU"]
        self.max_positions = 2
        self.take_profit = 0.10
        self.trailing_stop = 0.08
       
        # State tracking for exits
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
       
        # 1. Manage Active Positions (Exits)
        active_tickers = [ticker for ticker in holdings if holdings[ticker] > 0]
       
        for ticker in active_tickers:
            ticker_data = [row[ticker] for row in data["ohlcv"] if ticker in row]
            if not ticker_data:
                continue
               
            current_price = ticker_data[-1]["close"]
            entry_price = self.entry_prices.get(ticker, current_price)
           
            # Update high water mark for the trailing stop
            if ticker not in self.high_water_marks:
                self.high_water_marks[ticker] = current_price
            self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
           
            highest_price = self.high_water_marks[ticker]
           
            # Exit Logic
            if current_price >= entry_price * (1.0 + self.take_profit):
                allocations[ticker] = 0.0
                self.entry_prices.pop(ticker, None)
                self.high_water_marks.pop(ticker, None)
                log(f"TAKE PROFIT: {ticker} exit at {current_price}")
               
            elif current_price <= highest_price * (1.0 - self.trailing_stop):
                allocations[ticker] = 0.0
                self.entry_prices.pop(ticker, None)
                self.high_water_marks.pop(ticker, None)
                log(f"SWING STOP: {ticker} exit at {current_price}")
               
            else:
                # Freeze position at the strict 50% weight
                allocations[ticker] = 0.50
                frozen_weight += 0.50

        # 2. Scan for New Entries
        if len(active_tickers) < self.max_positions:
            candidates = {}
           
            for ticker in self.tickers:
                if ticker in active_tickers:
                    continue
                   
                ticker_data = [row[ticker] for row in data["ohlcv"] if ticker in row]
                df = pd.DataFrame(ticker_data)
               
                # Use a 200-period lookback to fit inside the sliding data window
                if len(df) < 200:
                    continue
               
                current_price = df['close'].iloc[-1]
               
                # Trigger 1: Intraday Momentum (12-period VWMA)
                df['vwma_12'] = ta.vwma(df['close'], df['volume'], length=12)
                vwap_bullish = current_price > df['vwma_12'].iloc[-1]
               
                # Trigger 2: Asset Momentum (MACD)
                macd = ta.macd(df['close'])
                if macd is not None and not macd.empty:
                    macd_bullish = macd['MACD_12_26_9'].iloc[-1] > macd['MACDs_12_26_9'].iloc[-1]
                else:
                    macd_bullish = False
               
                # Trigger 3: Predatory Volume (RVOL >= 1.8)
                df['vol_sma_20'] = ta.sma(df['volume'], length=20)
                rvol = df['volume'].iloc[-1] / df['vol_sma_20'].iloc[-1]
               
                # Trigger 4: Structural Filter (200-period SMA on 5min)
                df['sma_200'] = ta.sma(df['close'], length=200)
                macro_safe = current_price > df['sma_200'].iloc[-1]

                # Execution Filter
                if vwap_bullish and macro_safe and rvol >= 1.8 and macd_bullish:
                    candidates[ticker] = rvol

            # 3. Dynamic Remainder Execution
            if candidates:
                # Rank candidates by strongest RVOL spike
                sorted_candidates = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
               
                for ticker, rvol_score in sorted_candidates:
                    # Check capacity before firing
                    if len([k for k, v in allocations.items() if v > 0]) < self.max_positions:
                       
                        # Calculate exact remaining capital
                        remaining_weight = 1.0 - frozen_weight
                        target_weight = min(0.50, remaining_weight)
                       
                        if target_weight > 0:
                            allocations[ticker] = target_weight
                            frozen_weight += target_weight
                           
                            # Log entry for tracking
                            entry_px = df['close'].iloc[-1]
                            self.entry_prices[ticker] = entry_px
                            self.high_water_marks[ticker] = entry_px
                           
                            log(f"SWING ENTRY ({int(target_weight*100)}%): {ticker} | RVOL: {rvol_score:.2f}")

        return TargetAllocation(allocations)