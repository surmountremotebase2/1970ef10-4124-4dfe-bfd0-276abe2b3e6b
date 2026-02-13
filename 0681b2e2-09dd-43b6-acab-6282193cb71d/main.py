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
                            self.banned_assets.add(ticker)
                            del self.high_water_marks[ticker]
                    except: continue

        # --- PHASE 2: PANIC SWITCH (VXX) ---
        vxx_p = [day["VXX"]["close"] for day in ohlcv if "VXX" in day]
        if len(vxx_p) > 50 and vxx_p[-1] > (sum(vxx_p[-50:]) / 50) * 1.1:
            allocation_dict["SGOV"] = 1.0
            return TargetAllocation(allocation_dict)

        # --- PHASE 3: OFFENSIVE SELECTION (Relative Strength Logic) ---
        scores = {}
        spy_prices = [day["SPY"]["close"] for day in ohlcv if "SPY" in day]
        
        for t in self.offensive_assets:
            if t in self.banned_assets: continue
            
            prices = [day[t]["close"] for day in ohlcv if t in day]
            
            # We need at least 60 hours (~1.5 weeks) for a solid Relative Strength check
            if len(prices) > 60 and len(spy_prices) > 60:
                asset_perf = (prices[-1] - prices[-60]) / prices[-60]
                spy_perf = (spy_prices[-1] - spy_prices[-60]) / spy_prices[-60]
                
                # CRITICAL RULE: Asset MUST be outperforming the market (SPY)
                if asset_perf > spy_perf:
                    # Rank based on immediate 20-hour acceleration
                    scores[t] = (prices[-1] - prices[-20]) / prices[-20]

        top = sorted(scores, key=scores.get, reverse=True)[:2]
        
        if not top:
             allocation_dict["SGOV"] = 1.0 # Go to safety if nothing is beating SPY
        else:
            for asset in top:
                allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)