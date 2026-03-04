from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (THE CANNON v3 - MEAN REVERSION) ---
        # ACTION: Flipped engine from Breakout to Mean Reversion (Buy the Dip).
        # LOGIC: Buys assets that are dropping sharply intraday while the macro market (SPY) is green.
        
        self.tickers = ["TQQQ", "SOXL", "FNGU", "BITU"]
        self.safety = ["SGOV"]
        self.vixy = "VIXY" 
        self.spy = "SPY"

        # --- HYPER-AGGRESSIVE PARAMETERS ---
        self.vix_ma_len = 78 # 1 Day VIXY moving average
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
            log(f"CANNON v3 ACTIVE: Mean Reversion Engine. Buying the Dip.")
            self.debug_printed = True

        # 1. LOCKOUT CHECK 
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            if self.current_position != "SGOV":
                self.current_position = "SGOV"
                return TargetAllocation({"SGOV": 1.0})
            return None 

        # 2. INTRA-DAY VIXY SHIELD 
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

        # 3. DAILY SPY GOVERNOR CHECK (Macro Market Must Be Up)
        spy_hist = self.get_history(d, self.spy)
        spy_trend_down = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 4. SCORING & SELECTION (Find the asset bleeding the most)
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        # Sort lowest to highest to target the biggest negative momentum
        leader = sorted(scores, key=scores.get, reverse=False)[0]

        # A. ENTRY LOGIC
        if self.primary_asset is None:
            # ONLY BUY IF: SPY is green AND the asset has dropped at least 1.5% in the last hour
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
            
            # TIGHT STOPS RESTORED: Catch the bounce, lock it in, or cut the falling knife instantly
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