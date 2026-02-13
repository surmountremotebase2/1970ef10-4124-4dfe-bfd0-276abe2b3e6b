from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # 1. VENTURE NITRO UNIVERSE (The 300% Club)
        # High-Octane 3x Leverage & Crypto
        self.offensive_assets = ["SOXL", "DFEN", "FNGU", "BITX", "NVDL", "AGQ", "TECL"]
        
        # DEFENSIVE TRIAD (The "Escape" Options)
        # Cash (SGOV), Gold (IAU), Managed Futures (DBMF)
        self.defensive_assets = ["SGOV", "IAU", "DBMF"]
        
        self.indicators = ["VXX", "SPY"]
        # Combine all tickers for data fetching
        self.tickers = list(set(self.defensive_assets + self.offensive_assets + self.indicators))
        
        # 2. WIDE STOP LOSS MAP (Survival Mode)
        # Specific "leash" for each asset based on its volatility
        self.stop_loss_map = {
            "BITX": 0.25,  # 2x Bitcoin (25% Stop)
            "SOXL": 0.20,  # 3x Semis (20% Stop)
            "TECL": 0.20,  # 3x Tech (20% Stop)
            "FNGU": 0.20,  # 3x FANG+ (20% Stop)
            "NVDL": 0.18,  # 2x Nvidia (18% Stop)
            "AGQ":  0.15,  # 2x Silver (15% Stop)
            "DFEN": 0.15   # 3x Defense (15% Stop)
        }
        
        # Track High Water Marks for trailing stops
        self.high_water_marks = {} 

    @property
    def interval(self):
        # Run logic every hour to catch intraday moves
        return "1hour"

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        # Initialize allocation to 0% for all assets
        allocation_dict = {i: 0.0 for i in self.tickers}
        ohlcv = data["ohlcv"]
        current_positions = data["positions"]

        # --- PHASE 1: STOP LOSS CHECK (Intraday) ---
        banned_assets = []
        for ticker in self.offensive_assets:
            # Check if we currently hold the asset
            if ticker in current_positions and current_positions[ticker]["quantity"] > 0:
                current_price = ohlcv[-1][ticker]["close"]
                
                # Update High Water Mark
                if ticker not in self.high_water_marks:
                    self.high_water_marks[ticker] = current_price
                else:
                    self.high_water_marks[ticker] = max(self.high_water_marks[ticker], current_price)
                
                # Calculate Drawdown
                drawdown = (current_price - self.high_water_marks[ticker]) / self.high_water_marks[ticker]
                limit = self.stop_loss_map.get(ticker, 0.15)
                
                # Trigger Stop Logic
                if drawdown < -limit: 
                    log(f"🛑 STOP LOSS: {ticker} dropped {drawdown:.2%}. Selling.")
                    banned_assets.append(ticker)
                    del self.high_water_marks[ticker]

        # --- PHASE 2: PANIC SWITCH (VXX) ---
        # If Volatility is spiking > 5-day MA, go to CASH immediately.
        vxx_history = [x["close"] for x in ohlcv if x["symbol"] == "VXX"]
        if len(vxx_history) > 5:
            vxx_ma_5 = sum(vxx_history[-5:]) / 5
            if vxx_history[-1] > vxx_ma_5:
                log("🛡️ PANIC: VXX > 5d MA. 100% SGOV.")
                allocation_dict["SGOV"] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 3: BEAR HEDGE (SPY Trend) ---
        # If SPY < 200-day MA, pick the best Safety Asset (Gold vs Cash vs Managed Futures).
        spy_history = [x["close"] for x in ohlcv if x["symbol"] == "SPY"]
        if len(spy_history) > 200:
            spy_ma_200 = sum(spy_history[-200:]) / 200
            if spy_history[-1] < spy_ma_200:
                log("🐻 BEAR: SPY < 200d MA. Picking Best Haven...")
                
                # Compare 60-day performance
                def get_ret(t):
                    p = [x["close"] for x in ohlcv if x["symbol"] == t]
                    if len(p) < 60: return -999
                    return (p[-1] - p[-60]) / p[-60]

                safe_scores = {
                    "SGOV": get_ret("SGOV"), # Cash
                    "IAU":  get_ret("IAU"),  # Gold
                    "DBMF": get_ret("DBMF")  # Managed Futures
                }
                
                best_haven = max(safe_scores, key=safe_scores.get)
                log(f"🏆 Defensive Winner: {best_haven}")
                allocation_dict[best_haven] = 1.0
                return TargetAllocation(allocation_dict)

        # --- PHASE 4: OFFENSIVE ENGINE (Venture Nitro) ---
        # Market is safe. Pick Top 2 "Rocket Ships" based on 40-day momentum.
        scores = {}
        for ticker in self.offensive_assets:
            if ticker in banned_assets: continue
            prices = [x["close"] for x in ohlcv if x["symbol"] == ticker]
            if len(prices) > 40:
                scores[ticker] = (prices[-1] - prices[-40]) / prices[-40]
            else:
                scores[ticker] = -999

        top_assets = sorted(scores, key=scores.get, reverse=True)[:2]
        
        # 50/50 Split
        for asset in top_assets:
            allocation_dict[asset] = 0.5
            
        return TargetAllocation(allocation_dict)