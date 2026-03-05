from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- AGGRESSIVE GROWTH TARGET ENGINE ---
        # Goal: Maximum capital velocity. Capture 3x ETF intraday runners.
        
        self.tickers = ["TQQQ", "SOXL", "FNGU"] 
        self.safety = ["SGOV"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- AGGRESSIVE PARAMETERS (5-Min Interval) ---
        self.vix_ma_len = 78 # 1 Day VXX moving average (390 mins / 5)
        self.mom_len = 8 # 40-Minute Breakout Momentum (fast trigger)
        self.trend_len = 78 # 1 Day SPY Trend
        self.lockout_duration = 42 # 3.5 Hour Lockout (Prevent death by chop)
        
        # --- ASYMMETRIC RISK/REWARD ---
        self.hard_stop_pct = 0.045 # Cut loss at a strict 4.5% drop
        self.trail_activation_pct = 0.06 # Require a 6% gain to activate trail
        self.trail_pullback_pct = 0.025 # Trail peak by 2.5% once activated
        
        # State Tracking
        self.system_lockout_counter = 0
        self.primary_asset = None
        self.current_position = "SGOV" 
        self.entry_price = 0.0
        self.peak_price = 0.0
        self.debug_printed = False

    @property
    def interval(self):
        return "5min" # High frequency for aggressive scaling

    @property
    def assets(self):
        # Includes VXX and SPY to prevent fetch errors (the fixed ghost workaround)
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
            log(f"AGGRESSIVE ENGINE ACTIVE: 5-Min Timeframe. Asymmetric Risk Trailing Stop.")
            self.debug_printed = True

        # 1. CHOP LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.current_position != "SGOV":
                self.current_position = "SGOV"
                return TargetAllocation({"SGOV": 1.0})
            return None 

        # 2. VXX SHIELD (Volatility Spike Defense)
        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if len(vix_data) >= 2 and all(x["close"] > vix_ma for x in vix_data[-2:]):
                if self.primary_asset is not None:
                    log("EXIT: Volatility Spike. Engine Disengaged.")
                    self.system_lockout_counter = self.lockout_duration
                    self.primary_asset = None
                
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

        # 3. SPY GOVERNOR (Market must be healthy)
        spy_hist = self.get_history(d, self.spy)
        spy_trend_down = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 4. SCORING & SELECTION (Find the fastest mover)
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]

        # A. ENTRY LOGIC
        if self.primary_asset is None:
            # Entry requires positive momentum and a healthy broad market
            if scores[leader] > 0 and not spy_trend_down:
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                
                self.primary_asset = leader
                self.entry_price = self.get_history(d, leader)[-1]["close"]
                self.peak_price = self.entry_price
                self.current_position = leader
                
                log(f"ENTRY: Firing on {leader} at {self.entry_price}")
                return TargetAllocation({leader: 1.0})
            else:
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

        # B. ASYMMETRIC MANAGEMENT LOGIC
        p_hist = self.get_history(d, self.primary_asset)
        if p_hist:
            curr = p_hist[-1]["close"]
            self.peak_price = max(self.peak_price, curr)
            
            gain_from_entry = (curr - self.entry_price) / self.entry_price
            drop_from_peak = (self.peak_price - curr) / self.peak_price

            # THE RUNNER CAPTURE: Dynamic Trailing Stop
            if gain_from_entry >= self.trail_activation_pct:
                if drop_from_peak >= self.trail_pullback_pct:
                    log(f"TAKE PROFIT: Trailing stop secured runner on {self.primary_asset} at {curr}.")
                    self.system_lockout_counter = self.lockout_duration # Cool down after a win
                    self.primary_asset = None
                    if self.current_position != "SGOV":
                        self.current_position = "SGOV"
                        return TargetAllocation({"SGOV": 1.0})
                    return None

            # THE FLOOR: Absolute Hard Stop
            if gain_from_entry <= -self.hard_stop_pct:
                log(f"EXIT: Hard Stop Hit on {self.primary_asset}. Cutting loss.")
                self.system_lockout_counter = self.lockout_duration # 3.5 hour penalty box for a bad trade
                self.primary_asset = None
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

            return None
            
        return None