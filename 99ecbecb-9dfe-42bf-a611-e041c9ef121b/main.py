from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log

class TradingStrategy(Strategy):
    def __init__(self):
        # --- NITRO SERIES K (AGQ UPGRADE - CASH ACCOUNT OPTIMIZED) ---
        # ACTION: Bifurcated Equities vs. Alternatives.
        # ACTION: Expanded ATR to 14 Full Days (1092 periods) to adapt to highly leveraged volatility.
        # ACTION: Replaced Pandas ATR calculation with pure Python to prevent server timeouts.
        
        self.tickers_equity = ["SOXL", "FNGU", "DFEN"]
        self.tickers_alt = ["UCO", "URNM", "BITU", "AGQ"]
        self.tickers = self.tickers_equity + self.tickers_alt
        
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VXX" 
        self.spy = "SPY"

        # --- PARAMETERS ---
        self.vix_ma_len = 390 # 5 Days (390 * 5min)
        self.mom_len = 78 # Momentum Window (1 Full Day)
        self.trend_len = 156 # SPY Trend (2 Days)
        self.lockout_duration = 39 # 3.5 Hours
        self.atr_period = 1092 # 14 Full Trading Days (14 days * 78 candles)
        
        self.system_lockout_counter = 0
        self.primary_asset = None
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
        # OPTIMIZED: Pure Python math. Prevents engine lag when calculating 1092 periods.
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
            log(f"NITRO K: Bifurcated Governor Active. 14-Day Macro ATR Stops Applied.")
            self.debug_printed = True

        # 1. LOCKOUT CHECK (Churn Protection)
        if self.system_lockout_counter > 0:
            self.system_lockout_counter -= 1
            return TargetAllocation({"SGOV": 1.0})

        # 2. VXX SHIELD (3-Candle Confirmation & Lockout)
        vix_data = self.get_history(d, self.vixy)
        if len(vix_data) >= self.vix_ma_len:
            vix_ma = sum([x["close"] for x in vix_data[-self.vix_ma_len:]]) / self.vix_ma_len
            if len(vix_data) >= 3 and all(x["close"] > vix_ma for x in vix_data[-3:]):
                if self.primary_asset is not None:
                    log("EXIT: Sustained Volatility Spike. Lockdown Engaged.")
                    self.system_lockout_counter = self.lockout_duration
                    self.primary_asset = None
                return TargetAllocation({"SGOV": 1.0})

        # 3. SPY GOVERNOR CHECK (Trend Evaluation)
        spy_hist = self.get_history(d, self.spy)
        spy_trend_down = self.calculate_momentum(spy_hist, self.trend_len) < 0

        # 4. SCORING & SELECTION
        scores = {t: self.calculate_momentum(self.get_history(d, t), self.mom_len) for t in self.tickers}
        leader = sorted(scores, key=scores.get, reverse=True)[0]

        # A. ENTRY LOGIC
        if self.primary_asset is None:
            if scores[leader] > 0:
                # Governor blocks Equities only
                if leader in self.tickers_equity and spy_trend_down:
                    return TargetAllocation({"SGOV": 1.0})
                
                self.primary_asset = leader
                self.entry_price = self.get_history(d, leader)[-1]["close"]
                self.peak_price = self.entry_price
                log(f"ENTRY: {leader} at {self.entry_price}")
                return TargetAllocation({leader: 1.0})
            else:
                return TargetAllocation({"SGOV": 1.0})

        # B. MANAGEMENT LOGIC
        p_hist = self.get_history(d, self.primary_asset)
        if p_hist:
            curr = p_hist[-1]["close"]
            self.peak_price = max(self.peak_price, curr)
            
            # Use Macro ATR
            atr = self.calculate_atr(p_hist)
            if atr == 0:
                atr = curr * 0.02 # Fallback if sufficient history is unavailable
            
            # STOP LOSS (4.5x) or TRAILING STOP (8.0x)
            if curr <= self.entry_price - (4.5 * atr) or curr <= self.peak_price - (8.0 * atr):
                log(f"EXIT: {self.primary_asset} Stop/Trail Hit. Lockdown Engaged.")
                self.system_lockout_counter = self.lockout_duration
                self.primary_asset = None
                return TargetAllocation({"SGOV": 1.0})

            return TargetAllocation({self.primary_asset: 1.0})
            
        return None