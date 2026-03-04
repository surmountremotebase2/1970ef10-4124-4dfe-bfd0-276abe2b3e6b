from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (THE CANNON v2 - 30% DEPLOYMENT) ---
        # ACTION: Widened stops to 2.0x Hard / 4.0x Trail to fix Profit Factor bleed.
        
        self.tickers = ["TQQQ", "SOXL", "FNGU", "BITU"]
        self.safety = ["SGOV"]
        
        # Fixed ghost workaround to avoid VIXY ticker error in AI builder
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- HYPER-AGGRESSIVE PARAMETERS ---
        self.vix_ma_len = 78 # 1 Day VXX moving average (Highly sensitive)
        self.mom_len = 12 # 1 Hour Momentum (12 * 5min)
        self.trend_len = 78 # 1 Day SPY Trend (Only trades if the day is green)
        self.lockout_duration = 12 # 1 Hour Lockout after ejection
        self.atr_period = 78 # 1 Full Trading Day for tight intraday stops
        
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
            log(f"CANNON v2 ACTIVE: 2.0x Hard Stop, 4.0x ATR Trailing Stop.")
            self.debug_printed = True

        # 1. LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.current_position != "SGOV":
                self.current_position = "SGOV"
                return TargetAllocation({"SGOV": 1.0})
            return None 

        # 2. INTRA-DAY VXX SHIELD 
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

        # 3. DAILY SPY GOVERNOR CHECK (1-Day Trend)
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
            
            # WIDENED STOPS: 2.0x Hard Stop, 4.0x Trailing Stop
            if curr <= self.entry_price - (2.0 * atr) or curr <= self.peak_price - (4.0 * atr):
                log(f"EXIT: Cannon Stop/Trail Hit. Securing capital.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                
                if self.current_position != "SGOV":
                    self.current_position = "SGOV"
                    return TargetAllocation({"SGOV": 1.0})
                return None

            return None
            
        return None