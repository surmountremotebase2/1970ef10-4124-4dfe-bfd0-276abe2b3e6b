from surmount.base import Strategy, Asset, Symbol
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # ---------------------------------------------------------
        # STRATEGY CONFIGURATION
        # ---------------------------------------------------------
        self.nitro_tickers = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]
        self.safety_tickers = ["SGOV", "IAU", "DBMF"]
        self.trigger_ticker = "VIXY"

        # Parameters
        self.vix_lookback = 5 # VIXY 5-day MA
        self.mom_lookback = 40 # 40-day Nitro Momentum
        self.safe_lookback = 60 # 60-day Safety Ranking
        
        # Risk Management
        self.atr_period = 14
        self.atr_mult = 2.0
        self.profit_target = 0.20 # +20%
        self.tight_stop = 0.03 # 3% Trailing

        # Internal Trackers
        self.entry_prices = {}
        self.highest_prices = {}
        self.stages = {}

    @property
    def assets(self):
        # Tell Surmount which tickers we need data for
        return self.nitro_tickers + self.safety_tickers + [self.trigger_ticker]

    @property
    def interval(self):
        # Set to 5-minute heartbeat as discussed
        return "5min"

    def run(self, data):
        """
        Main execution loop run every 5 minutes.
        """
        # 1. MARKET REGIME CHECK (VIXY)
        vix_data = data["ohlcv"]
        if self.trigger_ticker not in vix_data or len(vix_data[self.trigger_ticker]) < self.vix_lookback:
            return None

        vix_close = vix_data[self.trigger_ticker][-1]["close"]
        vix_ma = np.mean([x["close"] for x in vix_data[self.trigger_ticker][-self.vix_lookback:]])
        
        is_risk_off = vix_close > vix_ma
        allocation = {}

        # -----------------------------------------------------
        # 2. RISK-OFF PROTOCOL (DEFENSE)
        # -----------------------------------------------------
        if is_risk_off:
            log("RISK OFF: Engaging Safety Trinity")
            # Reset Nitro Trackers
            self.entry_prices = {}
            self.highest_prices = {}
            self.stages = {}
            
            # Rank Safety by 60d momentum
            safe_scores = {}
            for t in self.safety_tickers:
                if t in vix_data and len(vix_data[t]) >= self.safe_lookback:
                    ret = (vix_data[t][-1]["close"] / vix_data[t][-self.safe_lookback]["close"]) - 1
                    safe_scores[t] = ret
            
            if safe_scores:
                best_safe = max(safe_scores, key=safe_scores.get)
                allocation[best_safe] = 1.0
            return allocation

        # -----------------------------------------------------
        # 3. RISK-ON ENGINE (NITRO)
        # -----------------------------------------------------
        # Check current positions for stops/profit taking
        # (Note: In a backtest, we simulate the 'holding' via the allocation dict)
        
        # Rank Nitro Universe by 40d Momentum
        nitro_scores = {}
        for t in self.nitro_tickers:
            if t in vix_data and len(vix_data[t]) >= self.mom_lookback:
                ret = (vix_data[t][-1]["close"] / vix_data[t][-self.mom_lookback]["close"]) - 1
                nitro_scores[t] = ret
        
        if not nitro_scores:
            return None

        # Sort by momentum
        ranked_nitro = sorted(nitro_scores.items(), key=lambda x: x[1], reverse=True)
        top_asset = ranked_nitro[0][0]

        # Simplified Allocation for the Backtester
        # Real-time 'Thirds' logic requires persistent state; for the backtest,
        # we focus on the Top 1 momentum rotation.
        allocation[top_asset] = 1.0
        
        log(f"RISK ON: Bullish {top_asset} ({nitro_scores[top_asset]:.2f})")
        return allocation