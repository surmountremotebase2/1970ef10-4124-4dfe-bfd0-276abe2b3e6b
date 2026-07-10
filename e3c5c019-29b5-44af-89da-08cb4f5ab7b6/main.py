from surmount.base_class import Strategy, TargetAllocation
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Initialize internal state tracker for the deadband
        self.last_target_weights = {}
        
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

        # 1. Asymmetric Entry / Exit Logic (Unchanged)
        for asset in assets:
            if asset in prices_df.columns and len(prices_df[asset]) >= 50:
                asset_data = prices_df[asset]
                current_price = asset_data.iloc[-1]
                
                sma_10 = asset_data.rolling(window=10).mean().iloc[-1]
                sma_20 = asset_data.rolling(window=20).mean().iloc[-1]
                sma_50 = asset_data.rolling(window=50).mean().iloc[-1]
                
                if sma_20 > sma_50 and current_price > sma_10:
                    valid_assets.append(asset)
                    
                    returns = asset_data.pct_change().dropna().tail(14)
                    volatility = returns.std() + 1e-8
                    inv_vol_scores[asset] = 1.0 / volatility
                    
        # 2. Risk Parity Calculation
        if not valid_assets:
            # If no valid assets, hard reset to Cash and clear internal memory
            allocation["SHV"] = 1.0
            self.last_target_weights = allocation
            return TargetAllocation(allocation)
            
        total_inv_vol = sum(inv_vol_scores.values())
        proposed_weights = {}
        
        for asset in valid_assets:
            raw_weight = inv_vol_scores[asset] / total_inv_vol
            proposed_weights[asset] = min(round(float(raw_weight), 4), 0.25)
            
        # 3. Internal State Deadband Logic
        deadband_threshold = 0.05
        
        # Ensure SHV has a baseline in memory
        if not self.last_target_weights:
             self.last_target_weights = {a: 0.0 for a in self.assets}
             self.last_target_weights["SHV"] = 1.0

        for asset in self.assets:
            if asset == "SHV":
                continue # Calculate SHV last
                
            current = self.last_target_weights.get(asset, 0.0)
            proposed = proposed_weights.get(asset, 0.0)
            
            # If an asset was valid yesterday but is invalid today (SMA 10 break),
            # it bypasses the deadband and is forced to 0.0.
            if asset not in valid_assets:
                allocation[asset] = 0.0
            # If weight drift exceeds 5%, execute the trade and update memory
            elif abs(proposed - current) > deadband_threshold:
                allocation[asset] = proposed
            # Otherwise, freeze the allocation at the last known state
            else:
                allocation[asset] = current
            
        # 4. Sweep remainder to Cash/SHV
        allocation["SHV"] = round(1.0 - sum([allocation[a] for a in valid_assets]), 4)
        
        # 5. Save the final allocation state to internal memory for tomorrow's calculation
        self.last_target_weights = dict(allocation)

        return TargetAllocation(allocation)