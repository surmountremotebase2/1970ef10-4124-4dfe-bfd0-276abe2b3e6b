from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
from surmount.technical_indicators import ATR, ADX

class TradingStrategy(Strategy):
    def __init__(self):
        # 1. THE VENTURE NITRO ASSET LIST
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        self.indicators = ["VXX", "SPY"]
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        
        self.atr_multiplier_map = {"BITX": 4.5, "SOXL": 4.0, "TECL": 4.0, "FNGU": 4.0, "NVDL": 3.5, "AGQ": 3.5, "DFEN": 3.0}
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
        raw_positions = data.get("holdings", data.get("positions", {}))
        
        def get_qty(ticker):
            val = raw_positions.get(ticker, 0)
            return float(val.get("quantity", 0)) if isinstance(val, dict) else float(val)

        # --- PHASE 1: ATR STOP LOSS ---
        if len(ohlcv) > 24:
            for ticker in self.offensive_assets:
                if get_qty(ticker) > 0 and ticker in ohlcv[-1]:
                    current_price = ohlcv[-1][ticker]["close"]
                    try:
                        atr = ATR(ticker, ohlcv, 14)[-1]
                        mult = self.atr_multiplier_map.get(ticker, 3.5)
                        if ticker not in self.high_water_marks: self.high_water_marks[ticker] = current_price
                        self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
                        if current_price < (self.high_water_marks[ticker] - (atr * mult)):
                            self.banned_