from surmount.base_class import Strategy, TargetAllocation
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Memory tracker for the signal state
        self.last_valid_assets = None

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
        active_assets = [a for a in self.assets if a != "SHV"]
        
        ohlcv = data.get("ohlcv", [])
        if len(ohlcv) < 55: 
            return TargetAllocation({"SHV": 1.0})

        close_prices = {}
        for asset in active_assets:
            closes = []
            for row in ohlcv:
                if asset in row:
                    closes.append(row[asset].get('close', 0))
            if closes:
                close_prices[asset] = pd.Series(closes)
        
        if not close_prices:
            return TargetAllocation({"SHV": 1.0})

        prices_df = pd.DataFrame(close_prices)
        current_valid_assets = []

        for asset in active_assets:
            if asset in prices_df.columns and len(prices_df[asset]) >= 50:
                asset_data = prices_df[asset]
                current_price = asset_data.iloc[-1]
                
                sma_10 = asset_data.rolling(window=10).mean().iloc[-1]
                sma_20 = asset_data.rolling(window=20).mean().iloc[-1]
                sma_50 = asset_data.rolling(window=50).mean().iloc[-1]
                
                if sma_20 > sma_50 and current_price > sma_10:
                    current_valid_assets.append(asset)
                    
        # Sort to ensure identical lists match correctly
        current_valid_assets.sort()

        # The Execution Kill Switch
        # If the approved assets are identical to yesterday, bypass Surmount's rebalance engine.
        if self.last_valid_assets is not None and current_valid_assets == self.last_valid_assets:
            return None 

        # Update memory for the next bar
        self.last_valid_assets = current_valid_assets
        
        allocation = {a: 0.0 for a in self.assets}
        
        if not current_valid_assets:
            allocation["SHV"] = 1.0
        else:
            fixed_weight = 0.20 
            for asset in current_valid_assets:
                allocation[asset] = fixed_weight
                
            allocation["SHV"] = round(1.0 - (len(current_valid_assets) * fixed_weight), 4)

        return TargetAllocation(allocation)