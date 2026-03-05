from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (FAST TRIGGER + 80/20 SPLIT) ---
        self.tickers = ["TQQQ", "SOXL", "FNGU"] 
        self.safety = ["SGOV"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS (5-Min Interval) ---
        self.vix_ma_len = 78 # 1 Day VXX moving average (390 mins / 5)
        
        # THE FIX: Fast Trigger. Shortened from 2 hours down to 30 minutes.
        self.mom_len = 6 # 30-Minute Breakout Momentum (30 mins / 5)
        
        self.trend_len = 78 # 1 Day SPY Trend
        self.lockout_duration = 12 # 1 Hour Lockout (60 mins / 5)
        
        # --- CASH ACCOUNT SPLIT ---
        self.trade_weight = 0.20 # Deploy 20% on the aggressive trade
        self.safety_weight = 0.80 # Park 80% in SGOV
        
        # State Tracking
        self.system_lockout_counter = 0
        self.primary_asset = None
        self.current_position = "SGOV" 
        self.entry_price = None
        self.peak_price = None
        self.debug_printed = False

    @property
    def interval(self):
        return "5min" # 5min interval to prevent backend fetch errors

    @property
    def assets(self):
        # Includes VXX and SPY to satisfy the fixed ghost workaround
        return self.tickers + self.safety + [self.vixy, self.spy]

    def get_history(self, d, ticker):
        history = []
        for bar in d:
            if ticker in bar:
                history.append(bar[ticker])
        return history

    def calculate_momentum(self, history, length):
        if len(history) >= length:
            return (history[-1]["close"] / history[-length]["close"]) - 1
        return -999

    def run(self, data):
        d = data["ohlcv"]
        if not d: return None
        
        if not self.debug_printed:
            log(f"ENGINE ACTIVE: 5-Min. Fast Trigger (30m). Entry-Only VXX. 80/20 Split.")
            self.debug_printed = True

        # 1. LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.current_position != "SGOV":
                self.current_position = "SGOV"
                return TargetAllocation({"SGOV": 1.0})
            return None 

        # 2. VXX SHIELD (Calculated here, applied ONLY to entries)
        vix_data = self.get_history(d, self.vixy)
        vix_spike = False
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if len(vix_data) >= 2 and all(x["close"] > vix_ma for x in vix_data[-2:]):
                vix_spike = True

        # 3. SPY GOVERNOR 
        spy_hist = self.get_history(d, self.spy)
        spy_trend_down = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 4. SCORING & SELECTION 
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]

        # A. ENTRY LOGIC 
        if self.primary_asset is None:
            # Entry requires positive 30-minute momentum, uptrending SPY, AND quiet VXX
            if scores[leader] > 0 and not spy_trend_down and not vix_spike:
                self.primary_asset = leader
                self.entry_price = self.get_history(d, leader)[-1]["close"]
                self.peak_price = self.entry_price
                self.current_position = leader
                
                log(f"ENTRY: Firing on {leader} at {self.entry_price} with 20% allocation")
                return TargetAllocation({leader: self.trade_weight, "SGOV": self.safety_weight})
            else:
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

        # B. MANAGEMENT LOGIC (Widened Fixed Percentage Stops)
        p_hist = self.get_history(d, self.primary_asset)
        if p_hist:
            curr = p_hist[-1]["close"]
            self.peak_price = max(self.peak_price, curr)
            
            # Calculate simple percentage movements
            gain_from_entry = (curr - self.entry_price) / self.entry_price
            drop_from_peak = (self.peak_price - curr) / self.peak_price

            # --- ASYMMETRIC TRAILING STOP ---
            # Wait for a 7% gain before activating the trail
            if gain_from_entry >= 0.07:
                # Once activated, exit if price drops 2.5% from the highest peak
                if drop_from_peak >= 0.025:
                    log(f"TAKE PROFIT: Trailing stop triggered on {self.primary_asset} at {curr}. Securing cash.")
                    self.system_lockout_counter = self.lockout_duration
                    self.primary_asset = None
                    if self.current_position != "SGOV":
                        self.current_position = "SGOV"
                        return TargetAllocation({"SGOV": 1.0})
                    return None

            # --- SAFETY NET: 5% Fixed Hard Stop ---
            if gain_from_entry <= -0.05:
                log(f"EXIT: Hard Stop Hit on {self.primary_asset}. Cutting losses.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

            return None
            
        return None