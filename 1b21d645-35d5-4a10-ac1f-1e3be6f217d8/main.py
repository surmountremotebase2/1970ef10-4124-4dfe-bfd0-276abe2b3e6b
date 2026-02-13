from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
from surmount.technical_indicators import ATR, ADX

class TradingStrategy(Strategy):
    def __init__(self):
        # 1. VENTURE NITRO UNIVERSE
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        self.indicators = ["VXX", "SPY"]
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        
        # 2. SMART STOP LOSS (ATR Multipliers)
        self.atr_multiplier_map = {
            "BITX": 3.5, "SOXL": 3.0, "TECL": 3.0, "FNGU": 3.0,
            "NVDL": 2.5, "AGQ":  2.5, "DFEN": 2.5
        }
        
        # Track High Water Marks
        self.high_water_marks = {} 
        # Persistent Penalty Box
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
        
        # --- FIX #1: Use .get("holdings") to prevent KeyError ---
        # This handles the error from your first screenshot
        current_positions = data.get("holdings", {})

        # --- PHASE 1: SMART ATR STOP LOSS ---
        for ticker in self.offensive_assets:
            if ticker in current_positions and current_positions[ticker]["quantity"] > 0:
                current_price = ohlcv[-1][ticker]["close"]
                
                # ATR Calculation with Safety Check
                if len(ohlcv) > 20:
                    try:
                        # Some versions of Surmount require 'high', 'low', 'close' explicitly
                        # We use a try/except block to default to 5% if ATR fails
                        atr_value = ATR(ticker, ohlcv, 14)[-1]
                    except:
                        atr_value = current_price * 0.05 
                else:
                    atr_value = current_price * 0.05 

                multiplier = self.atr_multiplier_map.get(ticker, 3.0)
                
                # Dynamic Stop Price Logic
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
        vxx_history = [x["close"] for x in ohlcv if x["symbol"] == "VXX"]
        if len(vxx_history) > 400:
            vxx_ma_long = sum(vxx_history[-400:]) / 400
            if vxx_history[-1] > vxx_ma_long:
                log("🛡️ PANIC: VXX Spike. 100% SGOV.")
                self.banned_assets.clear() 
                allocation_dict["SGOV"] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 3: BEAR HEDGE ---
        spy_history = [x["close"] for x in ohlcv if x["symbol"] == "SPY"]
        if len(spy_history) > 2000:
            spy_ma_trend = sum(spy_history[-2000:]) / 2000
            if spy_history[-1] < spy_ma_trend:
                # Bear Mode Logic
                def get_ret(t):
                    p = [x["close"] for x in ohlcv if x["symbol"] == t]
                    if len(p) < 400: return -999
                    return (p[-1] - p[-400]) / p[-400]

                safe_scores = {
                    "SGOV": get_ret("SGOV"),
                    "IAU":  get_ret("IAU"),
                    "DBMF": get_ret("DBMF")
                }
                best_haven = max(safe_scores, key=safe_scores.get)
                allocation_dict[best_haven] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 4: OFFENSIVE ENGINE ---
        scores = {}
        for ticker in self.offensive_assets:
            if ticker in self.banned_assets: continue 
            
            # CHOP FILTER (ADX)
            try:
                if len(ohlcv) > 20:
                    adx_val = ADX(ticker, ohlcv, 14)[-1]
                    if adx_val < 20:
                        scores[ticker] = -999 
                        continue
            except:
                pass 

            prices = [x["close"] for x in ohlcv if x["symbol"] == ticker]
            if len(prices) > 3000:
                scores[ticker] = (prices[-1] - prices[-3000]) / prices[-3000]
            else:
                if len(prices) > 0: scores[ticker] = (prices[-1] - prices[0]) / prices[0]
                else: scores[ticker] = -999

        # --- FIX #2: Correct Syntax for Sorting ---
        # This line was broken in your second screenshot. It is fixed now.
        top_assets = sorted(scores, key=scores.get, reverse=True)[:2]
        
        # Check if we have valid assets
        if not top_assets or scores.get(top_assets[0], -999) == -999:
             allocation_dict["SGOV"] = 1.0
        else:
            for asset in top_assets:
                allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)