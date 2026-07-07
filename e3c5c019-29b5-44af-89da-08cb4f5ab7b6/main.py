from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # 4-Ticker Macro Roster (Gold entirely removed)
        self.tickers = ["TECL", "SOXL", "UCO", "AGQ"]
        
        # Engine Parameters
        self.allocation_size = 0.50 
        self.max_positions = 2      
        
        # Breakout Strategy Parameters
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.breakout_tp = 0.10
        self.breakout_stop = 0.08
        
        # Capitulation (Dip) Strategy Parameters
        self.dip_tp = 0.15
        self.dip_stop = 0.03
        
        # Internal Memory Tracker
        self.active_positions = {}
        self.exited_tickers = [] 

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_signal(self, history):
        if len(history) < 200: 
            return 0, None
            
        df = pd.DataFrame(history)
        
        recent_df = df.tail(12)
        vwap_num = (recent_df['close'] * recent_df['volume']).sum()
        vwap_den = recent_df['volume'].sum()
        vwap = vwap_num / vwap_den if vwap_den > 0 else df['close'].iloc[-1]
        
        current_price = df['close'].iloc[-1]
        open_price = df['open'].iloc[-1]
        
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
        
        sma_macro = df['close'].tail(200).mean() 
        
        # Calculate 14-Period RSI with zero-division safeguard
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        
        avg_loss = avg_loss.replace(0, np.nan)
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.fillna(100) 
        current_rsi = rsi.iloc[-1]
        
        # Strategy 1: Standard Macro Trend Breakout
        standard_buy = (
            (current_price > vwap) and 
            (current_price > sma_macro) and 
            (rvol >= self.rvol_threshold)
        )
        
        # Strategy 2: Capitulation Dip Buy
        is_green = current_price > open_price
        is_stretched = current_price < (sma_macro * 0.85) 
        
        dip_buy = (
            is_green and 
            is_stretched and 
            (current_rsi < 20) and 
            (rvol >= 2.5)
        )
        
        if dip_buy:
            return rvol, "dip"
        elif standard_buy:
            return rvol, "breakout"
            
        return 0, None

    def run(self, data):
        d = data.get("ohlcv")
        if not d: 
            return None
        
        holdings = data.get("holdings", {})
        orders = data.get("orders", [])
        
        # --- FIXED GHOST WORKAROUND ---
        ghost_positions = []
        for order in orders:
            t = order.get("ticker") or order.get("symbol")
            
            # Explicit check to avoid syntax errors
            if not t or (t not in self.tickers): 
                continue 
            
            action = str(order.get("action") or order.get("side")).lower()
            if action == "buy" and holdings.get(t, 0) == 0:
                ghost_positions.append(t)
                log(f"GHOST DETECTED: {t} is in transit. Locking ticker.")

        # --- AMNESIA RECOVERY CIRCUIT BREAKER ---
        if holdings:
            for t in self.tickers:
                has_shares = holdings.get(t, 0) > 0
                is_untracked = t not in self.active_positions
                is_active = t not in self.exited_tickers
                has_room = len(self.active_positions) < self.max_positions
                
                if has_shares and is_untracked and is_active and has_room:
                    cp = d[-1][t]["close"] if t in d[-1] else 0
                    self.active_positions[t] = {
                        "entry_price": cp, 
                        "peak_price": cp, 
                        "strategy": "breakout"
                    }
                    log(f"AMNESIA RECOVERY: Resynced live position for {t}")

        self.exited_tickers = []
        state_changed = False

        # --- 1. DYNAMIC SWING MANAGEMENT ---
        for t, metrics in list(self.active_positions.items()):
            current_bar = d[-1].get(t)
            if not current_bar: 
                continue
            
            cp = current_bar["close"]
            strat_type = metrics.get("strategy", "breakout")
            
            tp_pct = self.dip_tp if strat_type == "dip" else self.breakout_tp
            stop_pct = self.dip_stop if strat_type == "dip" else self.breakout_stop
            
            if cp > metrics["peak_price"]:
                self.active_positions[t]["peak_price"] = cp
            
            # OFFENSIVE EXIT
            if cp >= metrics["entry_price"] * (1 + tp_pct):
                log(f"TAKE PROFIT ({strat_type}): {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

            # DEFENSIVE EXIT
            if cp <= metrics["peak_price"] * (1 - stop_pct):
                log(f"SWING STOP ({strat_type}): {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

        # --- 2. PREDATORY SELECTION ---
        if len(self.active_positions) < self.max_positions:
            scores = {}
            strat_types = {}
            
            for t in self.tickers:
                is_held = t in self.active_positions
                is_ghost = t in ghost_positions
                
                if is_held or is_ghost:
                    continue
                
                hist = [bar[t] for bar in d if t in bar]
                if len(hist) > 200:
                    score, s_type = self.get_signal(hist)
                    if score > 0:
                        scores[t] = score
                        strat_types[t] = s_type
            
            if scores:
                best_ticker = max(scores, key=scores.get)
                best_strat = strat_types[best_ticker]
                
                self.active_positions[best_ticker] = {
                    "entry_price": d[-1][best_ticker]["close"],
                    "peak_price": d[-1][best_ticker]["close"],
                    "strategy": best_strat
                }
                state_changed = True
                log(f"ENTRY ({best_strat}): {best_ticker} | RVOL: {scores[best_ticker]:.2f}")

        # --- 3. ALLOCATION EXECUTION (Dynamic Mapping) ---
        if state_changed:
            cash = holdings.get("CASH", 0)
            current_values = {}
            total_portfolio_value = cash
            
            for t in self.tickers:
                shares = holdings.get(t, 0)
                if shares > 0 and t in d[-1]:
                    asset_value = shares * d[-1][t]["close"]
                    current_values[t] = asset_value
                    total_portfolio_value += asset_value
            
            if total_portfolio_value <= 0:
                new_allocation = {t: self.allocation_size for t in self.active_positions}
                return TargetAllocation(new_allocation)
            
            new_allocation = {}
            for t in self.active_positions:
                if t in current_values and current_values[t] > 0:
                    new_allocation[t] = current_values[t] / total_portfolio_value
                else:
                    target_value = min(cash, total_portfolio_value * self.allocation_size)
                    new_allocation[t] = target_value / total_portfolio_value
                    
            return TargetAllocation(new_allocation)

        return None