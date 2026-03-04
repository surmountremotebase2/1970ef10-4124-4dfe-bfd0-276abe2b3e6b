from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (CANNON v3 - DEBUG EDITION) ---
        # ACTION: Stripped roster down to TQQQ only. Removed VXX/VIXY shield.
        # REASON: Isolating the 'Failed to fetch' data error to see if a specific ticker is breaking the server.
        
        self.tickers = ["TQQQ"] # Stripped SOXL, FNGU, and BITU for isolation testing
        self.safety = ["SGOV"]
        self.spy = "SPY"

        # --- HYPER-AGGRESSIVE PARAMETERS ---
        self.mom_len = 12 # 1 Hour Lookback (Looking for the dip)
        self.trend_len = 78 # 1 Day SPY Trend (Market must be green)
        self.lockout_duration = 12 # 1 Hour Lockout after ejection
        self.atr_period = 78 # 1 Full Trading Day
        
        self.system_lockout_counter = 0
        self.primary_asset = None
        self.current_position = "SGOV" 
        self.entry_price = None
        self.peak_price = None
        self.debug_printed = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        # Removed VIX tracker from the required assets list entirely
        return self.tickers + self.safety + [self.spy]

    def get_history(self, d, ticker):
        history = []
        for bar in d:
            if ticker in bar:
                history.append(bar[ticker])
        return history

    def calculate_atr(self, ticker_data):
        if len(ticker_data) < self.atr_period + 1: 
            return 0
        
        data = ticker_data[-(self.atr_period + 1):]
        true_ranges = []
        for i in range(1, len(data)):
            high = data[i]["high"]
            low = data[i]["low"]
            prev_close = data[i-1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        
        return sum(true_ranges) / self.atr_period

    def calculate_momentum(self, history, length):
        if len(history) >= length:
            return (history[-1]["close"] / history[-length]["close"]) - 1
        return -999

    def run(self, data):
        d = data["ohlcv"]
        if not d: return None
        
        if not self.debug_printed:
            log(f"CANNON v3 DEBUG ACTIVE: TQQQ Isolation Test. VXX Disabled.")
            self.debug_printed = True

        # 1. LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.current_position != "SGOV":
                self.current_position = "SGOV"
                return TargetAllocation({"SGOV": 1.0})
            return None 

        # --- VXX SHIELD TEMPORARILY REMOVED FOR DEBUGGING ---

        # 2. DAILY SPY GOVERNOR CHECK (Macro Market Must Be Up)
        spy_hist = self.get_history(d, self.spy)
        spy_trend_down = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 3. SCORING & SELECTION
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=False)[0]

        # A. ENTRY LOGIC
        if self.primary_asset is None:
            if not spy_trend_down and scores[leader] < -0.015:
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                
                self.primary_asset = leader
                self.entry_price = self.get_history(d, leader)[-1]["close"]
                self.peak_price = self.entry_price
                self.current_position = leader
                
                drop_pct = scores[leader] * 100
                log(f"ENTRY: Buying the dip on {leader} at {self.entry_price} (1hr drop: {drop_pct:.2f}%)")
                return TargetAllocation({leader: 1.0})
            else:
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

        # B. MANAGEMENT LOGIC
        p_hist = self.get_history(d, self.primary_asset)
        if p_hist:
            curr = p_hist[-1]["close"]
            self.peak_price = max(self.peak_price, curr)
            
            atr = self.calculate_atr(p_hist)
            if atr == 0:
                atr = curr * 0.02 
            
            if curr <= self.entry_price - (1.5 * atr) or curr <= self.peak_price - (3.0 * atr):
                log(f"EXIT: Cannon Stop/Trail Hit. Securing capital.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

            return None
            
        return None