from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
from surmount.technical_indicators import ATR, ADX

class TradingStrategy(Strategy):
    def __init__(self):
        # 1. THE VENTURE NITRO ASSET LIST
        # These are the leveraged positions designed for the 300% target
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        self.indicators = ["VXX", "SPY"]
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        
        # 2. DYNAMIC ATR MULTIPLIERS
        # Using volatility units instead of fixed percentages to allow assets to "breathe"
        self.atr_multiplier_map = {
            "BITX": 4.5, "SOXL": 4.0, "TECL": 4.0, "FNGU": 4.0,
            "NVDL": 3.5, "AGQ": 3.5, "DFEN": 3.0
        }
        
        self.high_water_marks = {} 
        self.banned_assets = set() # Persistent Penalty Box to prevent immediate rebuying

    @property
    def interval(self):
        # High-frequency 5-minute check for leveraged security
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        allocation_dict = {i: 0.0 for i in self.tickers}
        ohlcv = data["ohlcv"]
        
        # Robust data retrieval for the Surmount backtester
        raw_positions = data.get("holdings", data.get("positions", {}))
        
        def get_qty(ticker):
            val = raw_positions.get(ticker, 0)
            return float(val.get("quantity", 0)) if isinstance(val, dict) else float(val)

        # --- PHASE 1: DYNAMIC ATR STOP LOSS ---
        # Checks if current price is below (Recent High - Volatility Unit)
        if len(ohlcv) > 24: # 2-hour warm-up to gather initial volatility data
            for ticker in self.offensive_assets:
                if get_qty(ticker) > 0 and ticker in ohlcv[-1]:
                    current_price = ohlcv[-1][ticker]["close"]
                    try:
                        atr = ATR(ticker, ohlcv, 14)[-1]
                        mult = self.atr_multiplier_map.get(ticker, 3.5)
                        
                        if ticker not in self.high_water_marks:
                            self.high_water_marks[ticker] = current_price
                        self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
                        
                        if current_price < (self.high_water_marks[ticker] - (atr * mult)):
                            log(f"🛑 ATR EXIT: {ticker} hit stop. Sent to Penalty Box.")
                            self.banned_assets.add(ticker)
                            del self.high_water_marks[ticker]
                    except: continue

        # --- PHASE 2: PANIC SWITCH (VXX) ---
        # Immediate exit to cash (SGOV) if volatility spikes above its 5-day trend
        vxx_history = [day["VXX"]["close"] for day in ohlcv if "VXX" in day]
        if len(vxx_history) > 400: # Approx 5 trading days at 5min interval
            vxx_ma_long = sum(vxx_history[-400:]) / 400
            if vxx_history[-1] > vxx_ma_long:
                log("🛡️ PANIC: VXX Spike. 100% SGOV.")
                self.banned_assets.clear() # Clear bans for total reset
                allocation_dict["SGOV"] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 3: BEAR HEDGE (SPY Trend) ---
        # If market is down, pick the best of Cash (SGOV), Gold (IAU), or Futures (DBMF)
        spy_history = [day["SPY"]["close"] for day in ohlcv if "SPY" in day]
        if len(spy_history) > 2000: # Approx 1 month trend
            if spy_history[-1] < (sum(spy_history[-2000:]) / 2000):
                def get_ret(t):
                    p = [day[t]["close"] for day in ohlcv if t in day]
                    return (p[-1] - p[-400]) / p[-400] if len(p) >= 400 else -999

                h = {"SGOV": get_ret("SGOV"), "IAU": get_ret("IAU"), "DBMF": get_ret("DBMF")}
                allocation_dict[max(h, key=h.get)] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 4: VENTURE OFFENSE ---
        # Selection based on 40-Day Momentum and ADX Trend Strength Filter
        scores = {}
        for t in self.offensive_assets:
            if t in self.banned_assets: continue
            
            try: # Trend Strength Check (ADX > 20)
                if len(ohlcv) > 20 and ADX(t, ohlcv, 14)[-1] < 20: continue
            except: pass

            p = [day[t]["close"] for day in ohlcv if t in day]
            if len(p) > 3000: # 40-Day Momentum Lookback
                scores[t] = (p[-1] - p[-3000]) / p[-3000]

        top = sorted(scores, key=scores.get, reverse=True)[:2]
        if not top: allocation_dict["SGOV"] = 1.0
        else:
            for a in top: allocation_dict[a] = 0.5
            
        return TargetAllocation(allocation_dict)