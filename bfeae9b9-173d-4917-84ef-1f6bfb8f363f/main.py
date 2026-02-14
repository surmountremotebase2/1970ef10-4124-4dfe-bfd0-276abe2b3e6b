​You​
import pandas as pd
import numpy as np

# ---------------------------------------------------------
# STRATEGY CONFIGURATION (Final "Nitro-Rotator" Build)
# ---------------------------------------------------------
# The "Target 100k" Universe (Aggressive)
NITRO_ASSETS = ["SOXL", "FNGU", "DFEN", "UCO", "SILJ", "URNM", "IBIT"]

# The "Safety Trinity" (Defensive Shield)
SAFETY_ASSETS = ["SGOV", "IAU", "DBMF"]

# The Market Regime Trigger
TRIGGER_ASSET = "VIXY"

# Strategy Parameters
RISK_OFF_LOOKBACK = 5 # 5-day MA for VIXY Trigger (Fast Reaction)
MOMENTUM_LOOKBACK = 40 # 40-day Return for Nitro Selection (Aggressive Entry)
SAFETY_LOOKBACK = 60 # 60-day Return for Safety Selection (Stable Defense)
RUNNER_TREND_LOOKBACK = 50 # 50-day SMA (Trend Floor for the Runner)

# Risk Management & Profit Taking
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0 # Stage 1: Initial Stop width (Wide)
PROFIT_TARGET = 0.20 # +20% Gain triggers the "Bank & Tighten" logic
TIGHT_STOP_PCT = 0.03 # Stage 2: 3% Trailing Stop (Tight)

# ---------------------------------------------------------
# INITIALIZATION
# ---------------------------------------------------------
def initialize(context):
    """
    Called once at the start. Sets up the asset universes and tracking variables.
    """
    context.nitro_universe = [symbol(s) for s in NITRO_ASSETS]
    context.safety_universe = [symbol(s) for s in SAFETY_ASSETS]
    context.trigger = symbol(TRIGGER_ASSET)
    
    # Tracking Dictionaries for "Virtual Stops"
    # These persist between days to track our entry prices and stop levels
    context.entry_prices = {}    
    context.highest_prices = {} # Tracks the "High Water Mark" for trailing stops
    context.stages = {} # Tracks if position is Stage 1 (New) or Stage 2 (Runner)

    # Set Benchmark
    set_benchmark(symbol("SPY"))
    log("Nitro-Rotator Initialized. Target: $100k. Lookback: 40d.")

# ---------------------------------------------------------
# MAIN LOGIC LOOP (Daily Execution)
# ---------------------------------------------------------
def handle_data(context, data):
    """
    Called every interval. We execute logic based on the "Traffic Light" system.
    """
    # -----------------------------------------------------
    # 1. THE TRAFFIC LIGHT (Market Regime Check)
    # -----------------------------------------------------
    hist_vix = data.history(context.trigger, "close", RISK_OFF_LOOKBACK + 5, "1d")
    
    if len(hist_vix) < RISK_OFF_LOOKBACK:
        return # Not enough data yet
        
    vix_current = hist_vix.iloc[-1]
    vix_ma = hist_vix[-RISK_OFF_LOOKBACK:].mean()
    
    # LOGIC: If Current VIX > 5-day MA, we are RISK-OFF (Red Light).
    is_risk_off = vix_current > vix_ma

    if is_risk_off:
        execute_safety_protocol(context, data)
    else:
        execute_nitro_engine(context, data)

# ---------------------------------------------------------
# EXECUTION MODULES
# ---------------------------------------------------------
def execute_safety_protocol(context, data):
    """
    Risk-Off Logic: Close all Nitro positions, Buy Top 1 Safety Asset.
    """
    # 1. Panic Button: Close all Nitro positions
    for asset in context.nitro_universe:
        if data.portfolio.positions[asset].amount > 0:
            order_target_percent(asset, 0)
            clean_tracker(context, asset) # Reset trackers
            log(f"RISK OFF: Closing {asset.symbol}")

    # 2. Rank Safety Assets by 60d Stability
    scores = {}
    for asset in context.safety_universe:
        hist = data.history(asset, "close", SAFETY_LOOKBACK + 5, "1d")
        if len(hist) > SAFETY_LOOKBACK:
            # Simple Return: (Price / Price_60_days_ago) - 1
            scores[asset] = (hist.iloc[-1] / hist.iloc[0]) - 1
    
    # 3. Buy Top 1 Safety Asset
    if scores:
        best_safety = max(scores, key=scores.get)
        
        # Only trade if we aren't already 100% in this asset
        curr_val = data.portfolio.positions[best_safety].amount
        if curr_val == 0:
            # Close other safety assets first
            for s_asset in context.safety_universe:
                if s_asset != best_safety and data.portfolio.positions[s_asset].amount > 0:
                    order_target_percent(s_asset, 0)
            
            # Go 100% into the shield
            order_target_percent(best_safety, 1.0)
            log(f"DEFENSE: Buying 100% {best_safety.symbol} (Score: {scores[best_safety]:.2f})")

def execute_nitro_engine(context, data):
    """
    Risk-On Logic: Manage Stops, Take Profit, and Hunt for New Leaders.
    """
    # 1. Close Safety Positions (if any exist)
    for asset in context.safety_universe:
        if data.portfolio.positions[asset].amount > 0:
            order_target_percent(asset, 0)
            log(f"RISK ON: Selling Defense {asset.symbol}")

    # 2. MANAGE EXISTING POSITIONS (The "Ratchet" Logic)
    # We iterate through assets we currently own
    current_holdings = [pos for pos in context.portfolio.positions if context.portfolio.positions[pos].amount > 0 and pos in context.nitro_universe]
    
    for asset in current_holdings:
        current_price = data.current(asset, "close")
        qty = context.portfolio.positions[asset].amount
        
        # Initialize trackers if missing (safeguard)
        if asset not in context.entry_prices:
            context.entry_prices[asset] = context.portfolio.positions[asset].cost_basis
            context.highest_prices[asset] = current_price
            context.stages[asset] = 1 # Stage 1 = Initial Holding
        
        # Update High Water Mark
        if current_price > context.highest_prices[asset]:
            context.highest_prices[asset] = current_price
            
        # --- LOGIC A: CHECK PROFIT TAKING (+20%) ---
        pnl_pct = (current_price / context.entry_prices[asset]) - 1
        
        if context.stages[asset] == 1 and pnl_pct >= PROFIT_TARGET:
            # HIT +20% GAIN -> SELL 1/3
            log(f"PROFIT TAKE: {asset.symbol} hit +20%. Selling 1/3 and Tightening Stop.")
            
            # Sell 1/3 of CURRENT holding
            qty_to_sell = int(qty * 0.33)
            order(asset, -qty_to_sell)
            
            # Move to Stage 2 (The Runner)
            context.stages[asset] = 2 
            
        # --- LOGIC B: CHECK STOP LOSS ---
        stop_price = 0.0
        
        if context.stages[asset] == 1:
            # Stage 1: Wide 2x ATR Stop
            atr = calculate_atr(data, asset, ATR_PERIOD)
            stop_price = context.highest_prices[asset] - (ATR_MULTIPLIER * atr)
            
        elif context.stages[asset] == 2:
            # Stage 2: The "Runner" Logic
            # Use Tight 3% Trailing Stop to lock in the +20% win
            stop_price = context.highest_prices[asset] * (1.0 - TIGHT_STOP_PCT)
            
            # Optional: Check 50d SMA Trend (The "Moon Bag" check)
            # If price is ABOVE 50d SMA, we might respect the trend more than the 3% stop
            # But for safety, we respect the 3% trail first to avoid giving back gains.
                
        # Execute Stop
        if stop_price > 0 and current_price < stop_price:
            log(f"STOP LOSS: {asset.symbol} hit stop at {current_price} (Stop: {stop_price:.2f}). Closing.")
            order_target_percent(asset, 0)
            clean_tracker(context, asset)

    # 3. HUNT FOR NEW POSITIONS (The "Rotator")
    # If we have cash > 10%, hunt for the next best asset
    cash_weight = context.portfolio.cash / context.portfolio.portfolio_value
    
    if cash_weight > 0.10:
        scores = {}
        for asset in context.nitro_universe:
            # Anti-Churn: EXCLUDE assets we already hold
            if data.portfolio.positions[asset].amount > 0:
                continue
                
            hist = data.history(asset, "close", MOMENTUM_LOOKBACK + 5, "1d")
            if len(hist) > MOMENTUM_LOOKBACK:
                # 40-day Cumulative Return
                scores[asset] = (hist.iloc[-1] / hist.iloc[0]) - 1
        
        if scores:
            # Find Best Available Asset
            best_asset = max(scores, key=scores.get)
            
            log(f"HUNTING: Buying {best_asset.symbol} with available cash. Score: {scores[best_asset]:.2f}")
            
            # Buy with 95% of available cash
            amount_to_buy = context.portfolio.cash * 0.95
            order_value(best_asset, amount_to_buy)
            
            # Initialize Tracker for new buy
            context.entry_prices[best_asset] = data.current(best_asset, "close")
            context.highest_prices[best_asset] = data.current(best_asset, "close")
            context.stages[best_asset] = 1

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def calculate_atr(data, asset, window):
    """
    Calculates Average True Range (ATR) manually for volatility stops.
    """
    hist = data.history(asset, ["high", "low", "close"], window + 2, "1d")
    tr_list = []
    
    for i in range(1, len(hist)):
        high = hist["high"].iloc[i]
        low = hist["low"].iloc[i]
        prev_close = hist["close"].iloc[i-1]
        
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    
    if len(tr_list) == 0:
        return 0
    return np.mean(tr_list[-window:])

def clean_tracker(context, asset):
    """
    Removes asset from tracking dicts when sold.
    """
    if asset in context.entry_prices:
        del context.entry_prices[asset]
    if asset in context.highest_prices:
        del context.highest_prices[asset]
    if asset in context.stages:
        del context.stages[asset]

From: Joshua Sullivan <sully2240@hotmail.com>
Sent: Friday, February 13, 2026 6:39 PM
To: Joshua Sullivan <sully2240@hotmail.com>
Subject: Re: Re:
 
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