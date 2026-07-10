from surmount.base_class import Strategy, TargetAllocation
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    @property
    def interval(self):
        # Daily interval remains to capture the macro swing.
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

        # 1. Asymmetric Entry / Exit Logic
        for asset in assets:
            if asset in prices_df.columns and len(prices_df[asset]) >= 50:
                asset_data = prices_df[asset]
                current_price = asset_data.iloc[-1]
                
                sma_10 = asset_data.rolling(window=10).mean().iloc[-1]
                sma_20 = asset_data.rolling(window=20).mean().iloc[-1]
                sma_50 = asset_data.rolling(window=50).mean().iloc[-1]
                
                # SLOW ENTRY: 20-day must be above 50-day (Macro trend confirmed)
                # FAST EXIT: Price must remain above 10-day (Short-term momentum intact)
                if sma_20 > sma_50 and current_price > sma_10:
                    valid_assets.append(asset)
                    
                    # Risk Calculation for Position Sizing (14-day Volatility)
                    returns = asset_data.pct_change().dropna().tail(14)
                    volatility = returns.std() + 1e-8
                    inv_vol_scores[asset] = 1.0 / volatility
                    
        # 2. Risk Parity Allocation
        if not valid_assets:
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)
            
        total_inv_vol = sum(inv_vol_scores.values())
        
        for asset in valid_assets:
            raw_weight = inv_vol_scores[asset] / total_inv_vol
            # Hard cap single-asset exposure at 25% to prevent outsized drawdown
            allocation[asset] = min(round(float(raw_weight), 4), 0.25)
            
        # Remainder sweeps to Cash/SHV
        allocation["SHV"] = round(1.0 - sum([allocation[a] for a in valid_assets]), 4)

        return TargetAllocation(allocation)