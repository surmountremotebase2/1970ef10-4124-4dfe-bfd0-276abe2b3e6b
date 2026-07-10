from surmount.base_class import Strategy, TargetAllocation
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Memory tracker to kill Surmount's daily micro-churn
        self.last_target_assets = None

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
        
        # Only require 25 days of data to spin up the fast EMAs
        if len(ohlcv) < 25: 
            return TargetAllocation({"SHV": 1.0})

        close_prices = {}
        for asset in active_assets:
            closes = [row[asset].get('close', 0) for row in ohlcv if asset in row]
            if closes:
                close_prices[asset] = pd.Series(closes)

        if not close_prices:
            return TargetAllocation({"SHV": 1.0})

        prices_df = pd.DataFrame(close_prices)
        velocity_rank = {}

        # 1. Fast EMA Gate & Velocity Calculation
        for asset in active_assets:
            if asset in prices_df.columns and len(prices_df[asset]) >= 21:
                asset_data = prices_df[asset]
                current_price = asset_data.iloc[-1]
                
                # Exponential Moving Averages react violently to recent price action
                ema_9 = asset_data.ewm(span=9, adjust=False).mean().iloc[-1]
                ema_21 = asset_data.ewm(span=21, adjust=False).mean().iloc[-1]
                
                # Hard Gate: Short-term momentum must be leading medium-term momentum
                if ema_9 > ema_21:
                    # Velocity Rank: How far has it launched off the 21 EMA base?
                    velocity = (current_price - ema_21) / ema_21
                    velocity_rank[asset] = velocity

        # 2. Isolate the Top 2 Velocity Leaders
        sorted_assets = sorted(velocity_rank, key=velocity_rank.get, reverse=True)
        target_assets = sorted_assets[:2] 
        target_assets.sort() 

        # 3. Execution Kill Switch (Prevent Rebalance Churn)
        if self.last_target_assets is not None and target_assets == self.last_target_assets:
            return None
            
        self.last_target_assets = target_assets
        
        # 4. The 50/50 Allocation Mapping
        allocation = {a: 0.0 for a in self.assets}
        
        if len(target_assets) == 0:
            allocation["SHV"] = 1.0
        elif len(target_assets) == 1:
            allocation[target_assets[0]] = 0.50
            allocation["SHV"] = 0.50
        else:
            allocation[target_assets[0]] = 0.50
            allocation[target_assets[1]] = 0.50

        return TargetAllocation(allocation)