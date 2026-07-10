from surmount.base_class import Strategy, TargetAllocation
import pandas as pd

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
        active_assets = [a for a in self.assets if a != "SHV"]
        allocation = {a: 0.0 for a in self.assets}
        
        ohlcv = data.get("ohlcv", [])
        if len(ohlcv) < 55: 
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        close_prices = {}
        for asset in active_assets:
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

        for asset in active_assets:
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
                    
        # Fixed 20% allocation per valid asset to bypass Surmount's drift rebalancing
        fixed_weight = 0.20 
        
        for asset in valid_assets:
            allocation[asset] = fixed_weight
            
        # Remainder cleanly sweeps to SHV
        allocation["SHV"] = round(1.0 - (len(valid_assets) * fixed_weight), 4)

        return TargetAllocation(allocation)