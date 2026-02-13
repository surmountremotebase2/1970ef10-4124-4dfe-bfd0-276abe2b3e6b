from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # 1. VENTURE NITRO UNIVERSE (The 300% Club)
        # 3x Leverage & Crypto
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        
        # DEFENSIVE TRIAD (Cash, Gold, Managed Futures)
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        
        self.indicators = ["VXX", "SPY"]
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        
        # 2. STOP LOSS MAP (Venture Edition)
        # Logic: 5-minute check allows us to catch falling knives instantly.
        self.stop_loss_map = {
            "BITX": 0.25,  # 2x Bitcoin
            "SOXL": 0.20,  # 3x Semis
            "TECL": 0.20,  # 3x Tech
            "FNGU": 0.20,  # 3x FANG+
            "NVDL": 0.18,  # 2x Nvidia
            "AGQ":  0.15,  # 2x Silver 
            "DFEN": 0.15   # 3x Defense
        }
        
        self.high_water_marks = {} 

    @property
    def interval(self):
        # FIXED: Running every 5 minutes to manage 3x leverage risk
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        allocation_dict = {i: 0.0 for i in self.tickers}
        ohlcv = data["ohlcv"]
        current_positions = data["positions"]

        # --- PHASE 1: HIGH-SPEED STOP LOSS ---
        banned_assets = []
        for ticker in self.offensive_assets:
            if ticker in current_positions and current_positions[ticker]["quantity"] > 0:
                current_price = ohlcv[-1][ticker]["close"]
                
                # Update High Water Mark
                if ticker not in self.high_water_marks:
                    self.high_water_marks[ticker] = current_price
                else:
                    self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
                
                drawdown = (current_price - self.high_water_marks[ticker]) / self.high_water_marks[ticker]
                limit = self.stop_loss_map.get(ticker, 0.15)
                
                if drawdown < -limit: 
                    log(f"🛑 STOP LOSS ({self.interval}): {ticker} dropped {drawdown:.2%}. Selling.")
                    banned_assets.append(ticker)
                    del self.high_water_marks[ticker]

        # --- PHASE 2: PANIC SWITCH (VXX) ---
        # Adjusted for 5-min interval:
        # We look back ~400 periods (approx 5 trading days) to detect a real spike
        vxx_history = [x["close"] for x in ohlcv if x["symbol"] == "VXX"]
        if len(vxx_history) > 400:
            # 400-period MA on 5min chart approximates the 5-Day Trend
            vxx_ma_long = sum(vxx_history[-400:]) / 400
            
            if vxx_history[-1] > vxx_ma_long:
                log(f"🛡️ PANIC: VXX ({vxx_history[-1]:.2f}) > 5-Day Average ({vxx_ma_long:.2f}). 100% SGOV.")
                allocation_dict["SGOV"] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 3: BEAR HEDGE (SPY Trend) ---
        # Adjusted for 5-min interval:
        # Using 2000 periods (approx 1 month) as the "Market Health" filter
        spy_history = [x["close"] for x in ohlcv if x["symbol"] == "SPY"]
        if len(spy_history) > 2000:
            spy_ma_trend = sum(spy_history[-2000:]) / 2000
            
            if spy_history[-1] < spy_ma_trend:
                log("🐻 BEAR MODE: Market below monthly trend. Picking Best Haven...")
                
                # Compare momentum (last 1 week = ~400 periods)
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
            if ticker in banned_assets: continue
            prices = [x["close"] for x in ohlcv if x["symbol"] == ticker]
            
            # Lookback: ~3000 periods (approx 40 Days) on 5-min chart
            # 40 days * 78 bars = 3120 bars
            if len(prices) > 3000:
                scores[ticker] = (prices[-1] - prices[-3000]) / prices[-3000]
            else:
                # Fallback if data is short (using whatever we have)
                if len(prices) > 0:
                    scores[ticker] = (prices[-1] - prices[0]) / prices[0]
                else:
                    scores[ticker] = -999

        top_assets = sorted(scores, key=scores.get, reverse=True)[:2]
        
        for asset in top_assets:
            allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)