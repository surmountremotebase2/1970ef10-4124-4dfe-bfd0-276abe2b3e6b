from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    @property
    def interval(self):
        # Shifting to a daily interval completely eliminates intraday whipsaws, 
        # forcing the engine to respect the compounding nature of macro trends.
        return "1day"

    @property
    def assets(self):
        return ["SOXL", "TECL", "AGQ", "UCO", "GDXU", "SHV"] 

    @property
    def data(self):
        return []

    def run(self, data):
        assets = [a for a in self.assets if a != "SHV"]
        allocation = {a: 0.0 for a in self.assets}
        
        ohlcv = data.get("ohlcv", [])
        if len(ohlcv) < 55: 
            # Require 55 days to build the 50-day moving average cleanly
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        close_prices = {}
        for asset in assets:
            closes = []
            for row in ohlcv:
                if asset in row:
                    closes.append(row[asset].get('close', 0))
            if closes:
                close_prices[asset] = pd.Series(closes)
        
        if not close_prices:
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        prices_df = pd.DataFrame(close_prices)
        
        valid_assets = []
        inv_vol_scores = {}

        # 1. Macro-Trend Regime Filter
        for asset in assets:
            if asset in prices_df.columns and len(prices_df[asset]) >= 50:
                asset_data = prices_df[asset]
                current_price = asset_data.iloc[-1]
                sma_20 = asset_data.rolling(window=20).mean().iloc[-1]
                sma_50 = asset_data.rolling(window=50).mean().iloc[-1]
                
                # Hard Boundary: Price must be above both SMAs, and 20 SMA must be above 50 SMA.
                # This guarantees we only deploy capital during a confirmed macro uptrend,
                # killing the sideways whipsaw trades that destroyed the previous backtest.
                if current_price > sma_20 and sma_20 > sma_50:
                    valid_assets.append(asset)
                    
                    # 2. Risk Calculation (14-day Standard Deviation)
                    returns = asset_data.pct_change().dropna().tail(14)
                    volatility = returns.std() + 1e-8
                    inv_vol_scores[asset] = 1.0 / volatility
                    
        # 3. Dynamic Allocation Mapping
        if not valid_assets:
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)
            
        total_inv_vol = sum(inv_vol_scores.values())
        
        for asset in valid_assets:
            # Calculate risk-parity weight
            raw_weight = inv_vol_scores[asset] / total_inv_vol
            
            # Capping maximum single-asset exposure at 25% to prevent 
            # single-point catastrophic drawdowns from violent corrections.
            allocation[asset] = min(round(float(raw_weight), 4), 0.25)
            
        # Any remaining, unallocated capital flows safely into SHV
        allocation["SHV"] = round(1.0 - sum([allocation[a] for a in valid_assets]), 4)

        return TargetAllocation(allocation)