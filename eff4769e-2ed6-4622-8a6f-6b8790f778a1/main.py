from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (CANNON v4 - THE HARD TARGET / 5-MIN PATCH) ---
        # ACTION: Reverted interval to '5min' to bypass Surmount fetch error.
        # ACTION: Expanded lookbacks to simulate a slower, less noisy timeframe.
        # ACTION: Retained the strict 2.5% Take Profit to lock in daily wins.
        
        self.tickers = ["TQQQ", "SOXL", "FNGU"] 
        self.safety = ["SGOV"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS (Calibrated for slower 5-Min execution) ---
        self.vix_ma_len = 78 # 1 Day VXX moving average (78 * 5min)
        self.mom_len = 24 # 2 Hour Momentum breakout (24 * 5min = 120 mins)
        self.trend_len = 78 # 1 Day SPY Trend
        self.lockout_duration = 12 # 1 Hour Lockout 
        self.atr_period = 78 # 1 Full Trading Day ATR
        
        self.system_lockout_counter = 0
        self.primary_asset = None
        self.current_position = "SGOV" 
        self.entry_price = None
        self.peak_price = None
        self.debug_printed = False

    @property
    def interval(self):
        return "5min" # Restored to 5min to prevent 'Failed to Fetch' error

    @property
    def assets(self):
        return self.tickers + self.safety + [self.vixy, self.spy]

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
            log(f"CANNON v4 ACTIVE: Hard Target Engine (2.5% Take-Profit).")
            self.debug_printed = True

        # 1. LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.current_position != "SGOV":
                self.current_position = "SGOV"
                return TargetAllocation({"SGOV": 1.0})
            return None 

        # 2. VXX SHIELD 
        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if len(vix_data) >= 2 and all(x["close"] > vix_ma for x in vix_data[-2:]):
                if self.primary_asset is not None:
                    log("EXIT: Intraday Volatility Spike. Cannon Disengaged.")
                    self.system_lockout_counter = self.lockout_duration
                    self.primary_asset = None
                
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

        # 3. SPY GOVERNOR CHECK (Macro Market Must Be Up)
        spy_hist = self.get_history(d, self.spy)
        spy_trend_down = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 4. SCORING & SELECTION
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]

        # A. ENTRY LOGIC
        if self.primary_asset is None:
            if scores[leader] > 0 and not spy_trend_down:
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                
                self.primary_asset = leader
                self.entry_price = self.get_history(d, leader)[-1]["close"]
                self.peak_price = self.entry_price
                self.current_position = leader
                
                log(f"ENTRY: Cannon firing on {leader} at {self.entry_price}")
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
            
            # THE HARD TARGET: 2.5% Take Profit
            if curr >= self.entry_price * 1.025:
                log(f"TAKE PROFIT: Locked in 2.5% gain on {self.primary_asset} at {curr}. Securing cash.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

            # THE SAFETY NET: 1.5x Hard Stop / 3.0x Trailing Stop
            if curr <= self.entry_price - (1.5 * atr) or curr <= self.peak_price - (3.0 * atr):
                log(f"EXIT: Stop/Trail Hit on {self.primary_asset}. Cutting losses.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

            return None
            
        return None