from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    @property
    def interval(self):
        # We remain on the daily interval to capture macro compounding and ignore intraday noise.
        return "1day"

    @property
    def assets(self):
        return ["SOXL", "TECL", "AGQ", "UCO", "GDXU", "SHV"] 

    @property
    def data(self):
        return []

    def run(self, data):
        assets = [a for a in self.assets if a != "SHV"]
        
        # Initialize target allocations to 0
        target_allocation = {a: 0.0 for a in self.assets}
        
        # Pull current holdings to calculate deadbands
        current_holdings = data.get("holdings", {})
        total_account_value = data.get("account_value", 0.0)
        
        current_weights = {a: 0.0 for a in self.assets}
        if total_account_value > 0:
            for a in self.assets:
                current_weights[a] = current_holdings.get(a, 0.0) / total_account_value

        ohlcv = data.get("ohlcv", [])
        if len(ohlcv) < 55: 
            target_allocation["SHV"] = 1.0
            return TargetAllocation(target_allocation)

        close_prices = {}
        high_prices = {}
        low_prices = {}
        
        for asset in assets:
            closes = []
            highs = []
            lows = []
            for row in ohlcv:
                if asset in row:
                    closes.append(row[asset].get('close', 0))
                    highs.append(row[asset].get('high', 0))
                    lows.append(row[asset].get('low', 0))
            if closes:
                close_prices[asset] = pd.Series(closes)
                high_prices[asset] = pd.Series(highs)
                low_prices[asset] = pd.Series(lows)
        
        if not close_prices:
            target_allocation["SHV"] = 1.0
            return TargetAllocation(target_allocation)

        valid_assets = []
        inv_vol_scores = {}

        # 1. Macro-Trend Gate & Asymmetric Exit Logic
        for asset in assets:
            if asset in close_prices and len(close_prices[asset]) >= 50:
                closes = close_prices[asset]
                highs = high_prices[asset]
                lows = low_prices[asset]
                
                current_price = closes.iloc[-1]
                sma_20 = closes.rolling(window=20).mean().iloc[-1]
                sma_50 = closes.rolling(window=50).mean().iloc[-1]
                
                # --- Average True Range (ATR) Calculation ---
                tr1 = highs - lows
                tr2 = (highs - closes.shift(1)).abs()
                tr3 = (lows - closes.shift(1)).abs()
                true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr_14 = true_range.rolling(window=14).mean()
                
                # --- Chandelier Exit Calculation ---
                # We calculate the highest high over the last 22 days (roughly one trading month)
                highest_high = highs.rolling(window=22).max().iloc[-1]
                # Dynamic stop loss is set 3 ATRs below the recent peak
                chandelier_stop = highest_high - (atr_14.iloc[-1] * 3)
                
                # --- Execution Logic ---
                # Entry/Hold Condition: Price must be in a macro uptrend (SMAs) 
                # AND it must be above the dynamic trailing stop.
                if current_price > sma_20 and sma_20 > sma_50 and current_price > chandelier_stop:
                    valid_assets.append(asset)
                    
                    # Risk Calculation (14-day Standard Deviation) for Parity Layer
                    returns = closes.pct_change().dropna().tail(14)
                    volatility = returns.std() + 1e-8
                    inv_vol_scores[asset] = 1.0 / volatility
                    
        # 2. Risk Parity & Rebalance Deadband Logic
        if not valid_assets:
            target_allocation["SHV"] = 1.0
            return TargetAllocation(target_allocation)
            
        total_inv_vol = sum(inv_vol_scores.values())
        proposed_weights = {}
        
        for asset in valid_assets:
            raw_weight = inv_vol_scores[asset] / total_inv_vol
            # Hard cap single-asset exposure at 25%
            proposed_weights[asset] = min(round(float(raw_weight), 4), 0.25)
            
        # --- The 5% Deadband Execution ---
        # The engine will not trade unless the proposed weight differs from the 
        # current holding weight by more than 5%. This kills daily churn.
        deadband_threshold = 0.05 
        
        for asset in valid_assets:
            current = current_weights.get(asset, 0.0)
            proposed = proposed_weights.get