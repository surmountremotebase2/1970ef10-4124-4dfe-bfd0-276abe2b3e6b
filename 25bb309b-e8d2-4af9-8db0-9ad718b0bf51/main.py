from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO UNIVERSE (The Sword) ---
        # Optimized for the $10k to $100k goal with maximum torque
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]
        
        # --- SAFETY TRINITY (The Shield) ---
        # Diversified defensive assets to catch market crashes
        self.safety = ["SGOV", "IAU", "DBMF"]
        
        # --- MARKET REGIME TRIGGER ---
        self.vixy = "VIXY"

        # --- STRATEGY PARAMETERS ---
        self.vix_ma_len = 5 # Fast reaction to volatility spikes
        self.mom_len = 40 # Aggressive 40-day momentum look-back
        self.safe_len = 60 # Stable 60-day look-back for defensive ranking

    @property
    def interval(self):
        # The 5-minute heartbeat to filter market noise
        return "5min"

    @property
    def assets(self):
        # Mandatory: Tells the platform which data to fetch
        return self.tickers + self.safety + [self.vixy]

    def run(self, data):
        """
        Main logic loop executed every 5 minutes.
        """
        d = data["ohlcv"]
        
        # 1. DATA VALIDATION
        # Ensure VIXY data is available before making a regime decision
        if self.vixy not in d or len(d[self.vixy]) < self.vix_ma_len:
            return None

        # 2. CALCULATE MARKET REGIME (Traffic Light)
        # Using the current VIXY price vs its 5-day Moving Average
        vix_closes = [x["close"] for x in d[self.vixy][-self.vix_ma_len:]]
        vix_ma = sum(vix_closes) / len(vix_closes)
        current_vix = d[self.vixy][-1]["close"]

        allocation_dict = {}
        
        # 3. EXECUTE REGIME LOGIC
        if current_vix > vix_ma:
            # --- RISK OFF (Red Light) ---
            # Search for the strongest trending asset in the Safety Trinity
            log("Regime: Defensive (Safety Trinity)")
            scores = {}
            for t in self.safety:
                if t in d and len(d[t]) >= self.safe_len:
                    # Calculate 60-day Cumulative Return
                    scores[t] = (d[t][-1]["close"] / d[t][-self.safe_len]["close"]) - 1
            
            if scores:
                best_safe = max(scores, key=scores.get)
                allocation_dict = {best_safe: 1.0}
                log(f"Safety Selected: {best_safe}")
        else:
            # --- RISK ON (Green Light) ---
            # Search for the strongest momentum leader in the Nitro Universe
            log("Regime: Offensive (Nitro Engine)")
            scores = {}
            for t in self.tickers:
                if t in d and len(d[t]) >= self.mom_len:
                    # Calculate 40-day Cumulative Return
                    scores[t] = (d[t][-1]["close"] / d[t][-self.mom_len]["close"]) - 1
            
            if scores:
                best_nitro = max(scores, key=scores.get)
                allocation_dict = {best_nitro: 1.0}
                log(f"Nitro Selected: {best_nitro}")

        # 4. RETURN TARGET ALLOCATION
        # This matches the required Surmount return object
        if allocation_dict:
            return TargetAllocation(allocation_dict)
        
        return None