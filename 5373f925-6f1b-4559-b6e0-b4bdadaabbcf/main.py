from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Nitro Universe
        self.tickers = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]
        self.safety = ["SGOV", "IAU", "DBMF"]
        self.vixy = "VIXY"
        
        # Logic Parameters
        self.mom_len = 40
        self.vix_ma_len = 10 
        self.atr_period = 14
        
        # State Tracking
        self.entry_price = None
        self.peak_price = None
        self.scale_stage = 0 

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers + self.safety + [self.vixy]

    def calculate_atr(self, ticker_data):
        df = pd.DataFrame(ticker_data)
        high_low = df['high'] - df['low']
        high_cp = np.abs(df['high'] - df['close'].shift())
        low_cp = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period).mean().iloc[-1]

    def run(self, data):
        d = data["ohlcv"]
        holdings = data["holdings"]
        
        if self.vixy not in d or len(d[self.vixy]) < self.vix_ma_len:
            return None
            
        vix_closes = [x["close"] for x in d[self.vixy][-self.vix_ma_len:]]
        vix_ma = sum(vix_closes) / len(vix_closes)
        current_vix = d[self.vixy][-1]["close"]
        
        current_ticker = None
        for t in self.tickers:
            if t in holdings and holdings[t] > 0:
                current_ticker = t
                break

        # EXIT & RISK LOGIC
        if current_ticker:
            price = d[current_ticker][-1]["close"]
            atr = self.calculate_atr(d[current_ticker])
            
            if self.entry_price is None:
                self.entry_price = price
                self.peak_price = price
            
            self.peak_price = max(self.peak_price, price)
            
            # 2% Serial Stop
            if price <= self.entry_price * 0.98:
                log(f"SHIELD ACTIVATED: 2% Stop on {current_ticker}")
                self.entry_price = None
                return TargetAllocation({})

            # 2x ATR Trailing Stop
            if price <= self.peak_price - (2 * atr):
                log(f"SHIELD ACTIVATED: ATR Stop on {current_ticker}")
                self.entry_price = None
                return TargetAllocation({})

            # Scaling out in 1/3 increments
            profit_pct = (price / self.entry_price) - 1
            if profit_pct >= 0.05 and self.scale_stage == 0:
                self.scale_stage = 1
                log(f"BANKING: Scaling 1/3 profit on {current_ticker}")
                return TargetAllocation({current_ticker: 0.66})
            elif profit_pct >= 0.10 and self.scale_stage == 1:
                self.scale_stage = 2
                log(f"BANKING: Scaling 2/3 profit on {current_ticker}")
                return TargetAllocation({current_ticker: 0.33})

        # ENTRY & REGIME LOGIC
        if current_vix > vix_ma:
            return TargetAllocation({"SGOV": 1.0})
        else:
            if current_ticker is None:
                scores = {}
                for t in self.tickers:
                    if t in d and len(d[t]) >= self.mom_len:
                        scores[t] = (d[t][-1]["close"] / d[t][-self.mom_len]["close"]) - 1
                
                if scores:
                    best_asset = max(scores, key=scores.get)
                    self.scale_stage = 0 
                    log(f"OFFENSE: Entering {best_asset}")
                    return TargetAllocation({best_asset: 1.0})
        return None