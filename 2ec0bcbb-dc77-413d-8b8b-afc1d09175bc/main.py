from surmount.base import Strategy, Asset, Symbol
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- ASSET UNIVERSES ---
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VIXY"

        # --- PARAMETERS ---
        self.vix_ma_len = 5
        self.mom_len = 40 # Your requested 40-day aggressive momentum
        self.safe_len = 60

    @property
    def assets(self):
        # This tells the platform exactly what data to fetch
        return self.tickers + self.safety + [self.vixy]

    @property
    def interval(self):
        # Your requested 5-minute heartbeat
        return "5min"

    def run(self, data):
        # 1. MARKET REGIME (VIXY)
        d = data["ohlcv"]
        if self.vixy not in d or len(d[self.vixy]) < self.vix_ma_len:
            return None

        # Calculate VIXY 5-day MA
        vix_closes = [x["close"] for x in d[self.vixy][-self.vix_ma_len:]]
        vix_ma = sum(vix_closes) / len(vix_closes)
        current_vix = d[self.vixy][-1]["close"]

        # 2. DECIDE ALLOCATION
        allocation = {}
        
        if current_vix > vix_ma:
            # --- RISK OFF (SAFETY) ---
            log("Regime: Defensive")
            safe_scores = {}
            for t in self.safety:
                if t in d and len(d[t]) >= self.safe_len:
                    # 60-day Return
                    safe_scores[t] = (d[t][-1]["close"] / d[t][-self.safe_len]["close"]) - 1
            
            if safe_scores:
                best_safe = max(safe_scores, key=safe_scores.get)
                allocation[best_safe] = 1.0
        else:
            # --- RISK ON (NITRO) ---
            log("Regime: Offensive")
            nitro_scores = {}
            for t in self.tickers:
                if t in d and len(d[t]) >= self.mom_len:
                    # 40-day Return
                    nitro_scores[t] = (d[t][-1]["close"] / d[t][-self.mom_len]["close"]) - 1
            
            if nitro_scores:
                best_nitro = max(nitro_scores, key=nitro_scores.get)
                allocation[best_nitro] = 1.0

        return allocation