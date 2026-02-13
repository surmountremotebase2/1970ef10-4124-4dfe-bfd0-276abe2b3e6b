from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
from surmount.technical_indicators import ATR, ADX

class TradingStrategy(Strategy):
    def __init__(self):
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        self.indicators = ["VXX", "SPY"]
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        self.high_water_marks = {} 
        self.banned_assets = set()

    @property
    def interval(self):
        return "1hour" 

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        allocation_dict = {i: 0.0 for i in self.tickers}
        ohlcv = data["ohlcv"]
        
        # --- PHASE 1: VXX PANIC (Loosened for testing) ---
        vxx_p = [day["VXX"]["close"] for day in ohlcv if "VXX" in day]
        if len(vxx_p) > 50: # Reduced from 120
            # Only panic if VXX is 10% ABOVE its average
            if vxx_p[-1] > (sum(vxx_p[-50:]) / 50) * 1.1:
                allocation_dict["SGOV"] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 2: OFFENSIVE SELECTION (Fast Start) ---
        scores = {}
        for t in self.offensive_assets:
            prices = [day[t]["close"] for day in ohlcv if t in day]
            # Reduced lookback to 40 hours so it starts trading almost immediately
            if len(prices) > 40: 
                scores[t] = (prices[-1] - prices[-40]) / prices[-40]

        top = sorted(scores, key=scores.get, reverse=True)[:2]
        
        if not top:
             log("Still gathering data... staying in SGOV")
             allocation_dict["SGOV"] = 1.0
        else:
            for asset in top:
                allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)