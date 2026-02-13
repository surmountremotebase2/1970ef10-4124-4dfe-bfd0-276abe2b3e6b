from surmount.base_class import Strategy, TargetAllocation, backtest
from surmount.logging import log
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # 1. DEFINE OUR POOLS
        self.tickers = ["VXX", "SGOV", "SPY", "IAU", "DBMF", 
                        "SOXL", "USD", "TQQQ", "DFEN", "IBIT", "URNM", "BITX"]
        
        # 2. DEFINE THE AGGRESSIVE CANDIDATE POOL (THE 7 SEATS)
        self.offensive_pool = ["SOXL", "USD", "TQQQ", "DFEN", "IBIT", "URNM", "BITX"]
        self.secondary_pool = ["IAU", "SGOV", "DBMF"]

    @property
    def interval(self):
        return "1day" # Daily rebalancing for mid-day safety checks

    @property
    def assets(self):
        return self.tickers

    def run(self, data):
        d = data["ohlcv"]
        
        # --- LAYER 1: PRIMARY RISK-OFF (VXX GUARDRAIL) ---
        # If current VXX price > 5d Simple Moving Average
        vxx_history = [i["VXX"]["close"] for i in d]
        vxx_sma_5 = np.mean(vxx_history[-5:])
        current_vxx = vxx_history[-1]
        
        if current_vxx > vxx_sma_5:
            log("VXX TRIGGER: Moving to 100% SGOV")
            return TargetAllocation({"SGOV": 1.0})

        # --- LAYER 2: SECONDARY HEDGE (SPY MACRO FILTER) ---
        # If current SPY price < 200d Simple Moving Average
        spy_history = [i["SPY"]["close"] for i in d]
        spy_sma_200 = np.mean(spy_history[-200:])
        current_spy = spy_history[-1]
        
        if current_spy < spy_sma_200:
            log("BEAR MARKET FILTER: Sorting Secondary Hedge")
            # Sort IAU, SGOV, DBMF by 60-day Cumulative Return
            returns = {t: (d[-1][t]["close"] / d[-60][t]["close"]) - 1 for t in self.secondary_pool}
            top_2 = sorted(returns, key=returns.get, reverse=True)[:2]
            return TargetAllocation({top_2[0]: 0.5, top_2[1]: 0.5})

        # --- LAYER 3: OFFENSIVE ENGINE (NITRO) ---
        # Sort the 7 aggressive assets by 40-day Cumulative Return
        offensive_returns = {t: (d[-1][t]["close"] / d[-40][t]["close"]) - 1 for t in self.offensive_pool}
        top_offensive = sorted(offensive_returns, key=offensive_returns.get, reverse=True)[:2]
        
        log(f"NITRO ON: Leading Assets {top_offensive}")
        return TargetAllocation({top_offensive[0]: 0.5, top_offensive[1]: 0.5})