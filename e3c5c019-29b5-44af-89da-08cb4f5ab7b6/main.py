from surmount.base_class import Strategy, TargetAllocation
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
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
        
        if len(ohlcv) < 55: 
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

        for asset in active_assets:
            if asset in close_prices and len(close_prices[asset]) >= 50:
                current_price = close_prices[asset].iloc[-1]
                
                sma_50 = close_prices[asset].rolling(window=50).mean().iloc[-1]
                high_20 = high_prices[asset].iloc[-21:-1].max()
                low_5 = low_prices[asset].iloc[-6:-1].min()
                
                # ENTRY: Macro trend confirmed UP and price breaks 20-day high
                if current_price > sma_50 and current_price > high_20 and asset not in current_holdings:
                    current_holdings.append(asset)
                    
                # EXIT: Price collapses below 5-day low OR breaks macro trend support
                elif (current_price < low_5 or current_price < sma_50) and asset in current_holdings:
                    current_holdings.remove(asset)

        current_holdings.sort()
        
        # Kill Switch to prevent platform friction
        if self.active_positions == current_holdings:
            return None
            
        self.active_positions = current_holdings
        
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