import pandas as pd
import numpy as np

# Strategy Parameters
# Target and Stop structured to maintain an ~80% ratio (positive skew)
PROFIT_TARGET_PCT = 0.20  # 20% upside target to allow multi-month secular runs
ATR_STOP_MULTIPLIER = 25  # Scaled for daily ATR to act as a wide structural stop
MACRO_SMA_PERIOD = 200    # Macro trend filter period
FAST_MA_PERIOD = 12       # Trend entry fast moving average
SLOW_MA_PERIOD = 26       # Trend entry slow moving average

def initialize(context):
    # Set single asset universe
    context.asset = symbol("TQQQ")
    context.benchmark = symbol("QQQ")
    
    # Track state variables
    context.in_position = False
    context.entry_price = 0.0
    context.highest_price = 0.0

def handle_data(context, data):
    # Fetch historical daily data for TQQQ and QQQ (macro filter)
    hist_tqqq = data.history(context.asset, ['close', 'high', 'low', 'atr'], 250, '1d')
    hist_qqq = data.history(context.benchmark, ['close'], MACRO_SMA_PERIOD + 10, '1d')
    
    if len(hist_tqqq) < MACRO_SMA_PERIOD or len(hist_qqq) < MACRO_SMA_PERIOD:
        return

    current_price = hist_tqqq['close'].iloc[-1]
    current_atr = hist_tqqq['atr'].iloc[-1]
    
    # 1. Macro Regime Filter (The Defense)
    # Check if the broader Nasdaq-100 (QQQ) is above its 200-day SMA
    qqq_close = hist_qqq['close']
    qqq_sma_200 = qqq_close.rolling(window=MACRO_SMA_PERIOD).mean().iloc[-1]
    is_macro_bullish = qqq_close.iloc[-1] > qqq_sma_200

    # 2. Intermediate Momentum Signal (The Entry)
    # Fast vs Slow EMA crossing on daily bars to capture long-duration trends
    close_prices = hist_tqqq['close']
    fast_ema = close_prices.ewm(span=FAST_MA_PERIOD, adjust=False).mean()
    slow_ema = close_prices.ewm(span=SLOW_MA_PERIOD, adjust=False).mean()
    
    momentum_bullish = fast_ema.iloc[-1] > slow_ema.iloc[-1] and fast_ema.iloc[-2] <= slow_ema.iloc[-2]

    # 3. Exit and Risk Management Logic
    if context.in_position:
        # Update high-water mark for trailing stop calculation
        if current_price > context.highest_price:
            context.highest_price = current_price
            
        # Calculate dynamic ATR-scaled trailing stop threshold
        dynamic_stop_distance = current_atr * ATR_STOP_MULTIPLIER
        trailing_stop_price = context.highest_price - dynamic_stop_distance
        profit_target_price = context.entry_price * (1.0 + PROFIT_TARGET_PCT)
        
        # Check Exit Triggers
        hit_target = current_price >= profit_target_price
        hit_stop = current_price <= trailing_stop_price
        macro_breakdown = not is_macro_bullish  # Force exit if macro regime fails

        if hit_target or hit_stop or macro_breakdown:
            order_target_percent(context.asset, 0.0)
            context.in_position = False
            context.entry_price = 0.0
            context.highest_price = 0.0
            
    else:
        # Entry Trigger: Must have macro alignment AND intermediate momentum alignment
        if is_macro_bullish and momentum_bullish:
            order_target_percent(context.asset, 1.0)
            context.in_position = True
            context.entry_price = current_price
            context.highest_price = current_price