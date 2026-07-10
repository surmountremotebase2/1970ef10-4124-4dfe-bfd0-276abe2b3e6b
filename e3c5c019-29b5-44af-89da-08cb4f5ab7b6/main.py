from surmount.base_class import Strategy, TargetAllocation
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    @property
    def interval(self):
        return "1day"

    @property
    def assets(self):
        return ["SOXL", "TECL", "AGQ", "UCO", "GDXU", "SHV"] 

    @property
    def data(self):
        return []

    def run(self, data):
        assets = [a for a in self.assets if a != "SHV"]
        
        # 1. Initialize all allocations to 0.0 (Forces a hard exit if asset fails SMA test)
        allocation = {a: 0.0 for a in self.assets}
        
        # 2. Fetch current weights to calculate the deadband
        current_holdings = data.get("holdings", {})
        total_account_value = data.get("account_value", 0.0)
        current_weights = {a: 0.0 for a in self.assets}
        
        if total_account_value > 0:
            for a in self.assets:
                current_weights[a] = current_holdings.get(a, 0.0) / total_account_value

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

        # 3. Asymmetric Entry / Exit Logic
        for asset in assets:
            if asset in prices_df.columns and len(prices_df[asset]) >= 50:
                asset_data = prices_df[asset]
                current_price = asset_data.iloc[-1]
                
                sma_10 = asset_data.rolling(window=10).mean().iloc[-1]
                sma_20 = asset_data.rolling(window=20).mean().iloc[-1]
                sma_50 = asset_data.rolling(window=50).mean().iloc[-1]
                
                # SLOW ENTRY: 20-day > 50-day 
                # FAST EXIT: Price > 10-day 
                if sma_20 > sma_50 and current_price > sma_10:
                    valid_assets.append(asset)
                    
                    # 14-day Volatility Calculation
                    returns = asset_data.pct_change().dropna().tail(14)
                    volatility = returns.std() + 1e-8
                    inv_vol_scores[asset] = 1.0 / volatility
                    
        # 4. Risk Parity Allocation with 5% Deadband
        if not valid_assets:
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)
            
        total_inv_vol = sum(inv_vol_scores.values())
        proposed_weights = {}
        
        for asset in valid_assets:
            raw_weight = inv_vol_scores[asset] / total_inv_vol
            proposed_weights[asset] = min(round(float(raw_weight), 4), 0.25)
            
        deadband_threshold = 0.05
        
        for asset in valid_assets:
            current = current_weights.get(asset, 0.0)
            proposed = proposed_weights.get(asset, 0.0)
            
            # If the weight drift is strictly greater than 5%, rebalance.
            # Otherwise, freeze the current weight to kill the daily churn.
            if abs(proposed - current) > deadband_threshold:
                allocation[asset] = proposed
            else:
                allocation[asset] = current
            
        # 5. Remainder dynamically sweeps to Cash/SHV
        allocation["SHV"] = round(1.0 - sum([allocation[a] for a in valid_assets]), 4)

        return TargetAllocation(allocation)