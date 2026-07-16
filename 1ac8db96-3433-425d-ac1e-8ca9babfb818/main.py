from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Tradable Macro Roster (TECL removed)
        self.tickers = ["GDXU", "SOXL", "UCO", "AGQ"]
        
        # Broad Market Proxy (Monitored only, never traded)
        self.macro_proxy = "QQQ"
        
        # Core Bullet Parameters
        self.allocation_size = 0.50
        self.max_positions = 2      
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        
        # Internal State Tracker
        self.active_positions = {}

    @property
    def interval(self): return "5min"

    # Engine pulls data for the 4 tradable assets + QQQ proxy
    @property
    def assets(self): return self.tickers + [self.macro_proxy]

    def check_macro_environment(self, history):
        """ The Macro Master Switch: Evaluates broad market health """
        if len(history) < 200: return False
        df = pd.DataFrame(history)
        
        current_price = df['close'].iloc[-1]
        sma_macro = df['close'].tail(200).mean()
        
        # MACD calculation for the broad market proxy
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_bullish = macd_line.iloc[-1] > signal_line.iloc[-1]
        
        # Switch flips ON only if broad market is above 200-SMA and MACD is pushing up
        return (current_price > sma_macro) and macd_bullish

    def get_conviction_score(self, history):
        """ The Individual Asset Trigger """
        if len(history) < 200: return 0
        df = pd.DataFrame(history)
        
        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
        
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        sma_macro = df['close'].tail(200).mean()

        # MACD calculation for the specific asset
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_bullish = macd_line.iloc[-1] > signal_line.iloc[-1]
        
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold and macd_bullish:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
        
        # --- THE DATA SCRUBBER ---
        raw_holdings = data.get("holdings", {})
        holdings = {str(k).upper(): v for k, v in raw_holdings.items()}
        
        state_changed = False

        # --- PHASE 1: SWING MANAGEMENT (Exits) ---
        for t in list(self.active_positions.keys()):
            if t not in d[-1]: continue
            
            cp = d[-1][t]["close"]
            metrics = self.active_positions[t]
            
            if cp > metrics["peak_price"]:
                self.active_positions[t]["peak_price"] = cp
            
            # Take Profit Exit
            if cp >= metrics["entry_price"] * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {t} exit at {cp}.")
                del self.active_positions[t]
                state_changed = True
                continue

            # Trailing Stop Exit
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                del self.active_positions[t]
                state_changed = True
                continue

        # --- PHASE 2: THE MACRO MASTER SWITCH ---
        macro_safe = False
        qqq_hist = [bar[self.macro_proxy] for bar in d if self.macro_proxy in bar]
        if len(qqq_hist) > 0:
            macro_safe = self.check_macro_environment(qqq_hist)

        # --- PHASE 3: PREDATORY SELECTION (Entries) ---
        # The engine will ONLY look for entries if the QQQ Master Switch is 'macro_safe'
        if len(self.active_positions) < self.max_positions and macro_safe:
            scores = {}
            for t in self.tickers:
                # DUPLICATE SHIELD: Block if active in memory OR physical holdings > 0.01
                if t in self.active_positions or holdings.get(t, 0) > 0.01:
                    continue
                
                hist = [bar[t] for bar in d if t in bar]
                if len(hist) > 0:
                    score = self.get_conviction_score(hist)
                    if score > 0:
                        scores[t] = score
            
            if scores:
                best_ticker = max(scores, key=scores.get)
                self.active_positions[best_ticker] = {
                    "entry_price": d[-1][best_ticker]["close"],
                    "peak_price": d[-1][best_ticker]["close"]
                }
                log(f"SWING ENTRY (50%): {best_ticker} | RVOL: {scores[best_ticker]:.2f}")
                state_changed = True

        # --- PHASE 4: NATIVE ALLOCATION (NO AUTO-BALANCE) ---
        if state_changed:
            alloc = {}
            cash = holdings.get("CASH", 0)
            
            # Calculate total account equity to lock in exact existing weights
            total_equity = cash
            for ticker, shares in holdings.items():
                # Explicitly ignore the proxy from allocation math if it ever accidentally registers
                if ticker in self.tickers and ticker in d[-1] and shares > 0.01:
                    total_equity += shares * d[-1][ticker]["close"]
            
            for t in self.active_positions:
                # If we physically hold the asset already, freeze its exact current weight to stop the fractional trim
                if holdings.get(t, 0) > 0.01 and total_equity > 0 and t in d[-1]:
                    current_weight = (holdings[t] * d[-1][t]["close"]) / total_equity
                    alloc[t] = current_weight
                else:
                    # If it is a new entry bullet, target the clean 50% allocation
                    alloc[t] = self.allocation_size
                    
            return TargetAllocation(alloc)

        return None