from surmount.base_class import Strategy, TargetAllocation
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Internal state to track active breakout positions
        self.active_positions = []

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
        
        if len(ohlcv) < 25: 
            return TargetAllocation({"SHV": 1.0})

        close_prices = {}
        high_prices = {}
        low_prices = {}
        
        for asset in active_assets:
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
            return TargetAllocation({"SHV": 1.0})

        current_holdings = list(self.active_positions)

        # 1. Pure Price Action Breakout (Donchian Logic)
        for asset in active_assets:
            if asset in close_prices and len(close_prices[asset]) >= 21:
                current_price = close_prices[asset].iloc[-1]
                
                # Highest high of the previous 20 days (excluding today)
                high_20 = high_prices[asset].iloc[-21:-1].max()
                # Lowest low of the previous 10 days (excluding today)
                low_10 = low_prices[asset].iloc[-11:-1].min()
                
                # ENTRY: Price breaches the 20-day high
                if current_price > high_20 and asset not in current_holdings:
                    current_holdings.append(asset)
                    
                # EXIT: Price collapses below the 10-day low
                elif current_price < low_10 and asset in current_holdings:
                    current_holdings.remove(asset)

        # 2. Sync Internal State
        current_holdings.sort()
        
        # Kill Switch: Bypass rebalance if holdings haven't changed
        if self.active_positions == current_holdings:
            return None
            
        self.active_positions = current_holdings
        
        # 3. Capital Allocation
        allocation = {a: 0.0 for a in self.assets}
        
        if not current_holdings:
            allocation["SHV"] = 1.0
        else:
            weight = round(1.0 / len(current_holdings), 4)
            for asset in current_holdings:
                allocation[asset] = weight
                
            allocated_total = sum([allocation[a] for a in current_holdings])
            if allocated_total < 1.0:
                allocation["SHV"] = round(1.0 - allocated_total, 4)

        return TargetAllocation(allocation)