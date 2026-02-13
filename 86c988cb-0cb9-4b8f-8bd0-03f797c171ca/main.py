from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
from surmount.technical_indicators import ATR

class TradingStrategy(Strategy):
    def __init__(self):
        # The core assets that actually move the needle
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        self.tickers = list(set(self.offensive_assets + self.defensive_assets + ["VXX", "SPY"]))
        
        # Original ATR Multipliers that gave us the 222% run
        self.atr_multiplier_map = {"BITX": 4.5, "SOXL": 4.0, "TECL": 4.0, "FNGU": 4.0, "NVDL": 3.5, "AGQ": 3.5, "DFEN": 3.0}
        self.high_water_marks = {} 
        self.banned_assets = set()

    @property
    def interval(self): return "1hour" 

    @property
    def assets(self): return self.tickers

    def run(self, data):
        allocation_dict = {i: 0.0 for i in self.tickers}
        ohlcv = data["ohlcv"]
        
        # --- 1. DYNAMIC ATR STOP LOSS ---
        raw_positions = data.get("holdings", data.get("positions", {}))
        for ticker in self.offensive_assets:
            if ticker in ohlcv[-1]:
                current_price = ohlcv[-1][ticker]["close"]
                if ticker not in self.high_water_marks: self.high_water_marks[ticker] = current_price
                self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
                
                try:
                    atr = ATR(ticker, ohlcv, 14)[-1]
                    if current_price < (self.high_water_marks[ticker] - (atr * self.atr_multiplier_map.get(ticker, 3.5))):
                        self.banned_assets.add(ticker)
                except: continue

        # --- 2. THE RESTORED MOMENTUM ENGINE (NO SPY FILTER) ---
        scores = {}
        for t in self.offensive_assets:
            if t in self.banned_assets: continue
            prices = [day[t]["close"] for day in ohlcv if t in day]
            # Back to 40-hour lookback: Long enough to trend, short enough to catch moves
            if len(prices) > 40:
                scores[t] = (prices[-1] - prices[-40]) / prices[-40]

        top = sorted(scores, key=scores.get, reverse=True)[:2]
        
        # Only trade if the top asset is actually POSITIVE
        if not top or scores[top[0]] <= 0:
            allocation_dict["SGOV"] = 1.0
        else:
            for asset in top:
                allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)