from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
from surmount.technical_indicators import ATR, ADX

class TradingStrategy(Strategy):
    def __init__(self):
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        self.indicators = ["VXX", "SPY"]
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        
        # WIDENED ATR MULTIPLIERS: Giving the "Nitro" more room to breathe
        self.atr_multiplier_map = {
            "BITX": 4.0, "SOXL": 3.5, "TECL": 3.5, "FNGU": 3.5,
            "NVDL": 3.0, "AGQ":  3.0, "DFEN": 3.0
        }
        
        self.high_water_marks = {} 
        self.banned_assets = set()

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        allocation_dict = {i: 0.0 for i in self.tickers}
        ohlcv = data["ohlcv"]
        raw_positions = data.get("holdings", data.get("positions", {}))
        
        def get_quantity(ticker):
            if ticker not in raw_positions: return 0.0
            val = raw_positions[ticker]
            return float(val.get("quantity", 0.0)) if isinstance(val, dict) else float(val)

        # --- PHASE 1: SMART ATR STOP LOSS (With Warm-up) ---
        for ticker in self.offensive_assets:
            qty = get_quantity(ticker)
            if qty > 0:
                if len(ohlcv) > 0 and ticker in ohlcv[-1]:
                    current_price = ohlcv[-1][ticker]["close"]
                else:
                    continue 

                # WARM-UP CHECK: Don't stop out in the first 12 bars (1 hour)
                if len(ohlcv) < 12:
                    continue

                try:
                    atr_value = ATR(ticker, ohlcv, 14)[-1]
                except:
                    atr_value = current_price * 0.05 

                multiplier = self.atr_multiplier_map.get(ticker, 3.0)
                
                if ticker not in self.high_water_marks:
                    self.high_water_marks[ticker] = current_price
                else:
                    self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
                
                stop_price = self.high_water_marks[ticker] - (atr_value * multiplier)
                
                if current_price < stop_price: 
                    log(f"🛑 ATR STOP: {ticker} Price {current_price:.2f} < Stop {stop_price:.2f}. Selling.")
                    self.banned_assets.add(ticker)
                    del self.high_water_marks[ticker]

        # --- PHASE 2: PANIC SWITCH (VXX) ---
        vxx_history = [day["VXX"]["close"] for day in ohlcv if "VXX" in day]
        if len(vxx_history) > 400:
            vxx_ma_long = sum(vxx_history[-400:]) / 400
            if vxx_history[-1] > vxx_ma_long:
                log("🛡️ PANIC: VXX Spike. 100% SGOV.")
                self.banned_assets.clear() 
                allocation_dict["SGOV"] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 3: BEAR HEDGE ---
        spy_history = [day["SPY"]["close"] for day in ohlcv if "SPY" in day]
        if len(spy_history) > 2000:
            spy_ma_trend = sum(spy_history[-2000:]) / 2000
            if spy_history[-1] < spy_ma_trend:
                def get_ret(t):
                    p = [day[t]["close"] for day in ohlcv if t in day]
                    return (p[-1] - p[-400]) / p[-400] if len(p) >= 400 else -999

                safe_scores = {"SGOV": get_ret("SGOV"), "IAU": get_ret("IAU"), "DBMF": get_ret("DBMF")}
                best_haven = max(safe_scores, key=safe_scores.get)
                allocation_dict[best_haven] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 4: OFFENSIVE ENGINE ---
        scores = {}
        for ticker in self.offensive_assets:
            if ticker in self.banned_assets: continue 
            
            try:
                if len(ohlcv) > 20 and ADX(ticker, ohlcv, 14)[-1] < 20:
                    continue
            except:
                pass 

            prices = [day[ticker]["close"] for day in ohlcv if ticker in day]
            scores[ticker] = (prices[-1] - prices[-3000]) / prices[-3000] if len(prices) > 3000 else -999

        top_assets = sorted(scores, key=scores.get, reverse=True)[:2]
        
        if not top_assets or scores.get(top_assets[0], -999) == -999:
             allocation_dict["SGOV"] = 1.0
        else:
            for asset in top_assets:
                allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)