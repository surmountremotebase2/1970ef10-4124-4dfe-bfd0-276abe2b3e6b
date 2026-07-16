from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Original 5-Ticker Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
       
        # Dual-Bullet Parameters
        self.allocation_size = 0.50
        self.max_positions = 2      
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
       
        # Internal Memory Tracker
        self.active_positions = {}
        self.exited_tickers = []
        
        # Geopolitical Cooldown Registry
        self.cooldown_positions = {}

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 200: return 0
        df = pd.DataFrame(history)
       
        recent_df = df.tail(self.vwap_len)
        vwap = (recent_df['close'] * recent_df['volume']).sum() / recent_df['volume'].sum()
        current_price = df['close'].iloc[-1]
       
        avg_vol = df['volume'].tail(20).mean()
        rvol = df['volume'].iloc[-1] / avg_vol if avg_vol > 0 else 0
       
        sma_macro = df['close'].tail(200).mean()
       
        if current_price > vwap and current_price > sma_macro and rvol >= self.rvol_threshold:
            return rvol
        return 0

    def run(self, data):
        d = data.get("ohlcv")
        if not d: return None
       
        # --- PHASE 1: THE DATA SCRUBBER ---
        # Guarantees API data is uppercase, permanently killing the duplicate buy glitch
        raw_holdings = data.get("holdings", {})
        holdings = {str(k).upper(): v for k, v in raw_holdings.items()}
       
        # --- PHASE 2: AMNESIA RECOVERY CIRCUIT BREAKER ---
        if holdings:
            for t in self.tickers:
                # Bypasses fractional share dust by checking for structural positions (> 0.01 shares)
                if holdings.get(t, 0) > 0.01 and t not in self.active_positions:
                    if t not in self.exited_tickers and len(self.active_positions) < self.max_positions:
                        cp = d[-1][t]["close"] if t in d[-1] else 0
                        self.active_positions[t] = {"entry_price": cp, "peak_price": cp}
                        log(f"AMNESIA RECOVERY: Resynced live position for {t}")

        self.exited_tickers = []
        state_changed = False

        # --- PHASE 3: SWING MANAGEMENT ---
        for t, metrics in list(self.active_positions.items()):
            current_bar = d[-1].get(t)
            if not current_bar: continue
           
            cp = current_bar["close"]
           
            if cp > metrics["peak_price"]:
                self.active_positions[t]["peak_price"] = cp
           
            # OFFENSIVE EXIT: 10% Target
            if cp >= metrics["entry_price"] * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                self.cooldown_positions[t] = len(d) # Log current dataset length for cooldown tracking
                del self.active_positions[t]
                state_changed = True
                continue

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                self.cooldown_positions[t] = len(d) # Log current dataset length for cooldown tracking
                del self.active_positions[t]
                state_changed = True
                continue

        # --- PHASE 4: PREDATORY SELECTION ---
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                # Whipsaw Shield: 78 bars of 5-min intervals equals 1 full trading day of quarantine
                if t in self.cooldown_positions and (len(d) - self.cooldown_positions[t]) < 78:
                    continue
                
                # The Sieve: Prevent buying if tracked in memory OR if physical inventory exists
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
                state_changed = True
                log(f"SWING ENTRY (50%): {best_ticker} | RVOL: {scores[best_ticker]:.2f}")

        # --- PHASE 5: ENVIRONMENT-ADAPTIVE ALLOCATION ---
        if state_changed:
            # LIVE ENGINE SWITCH: Executes if the real account 'CASH' key is present
            if "CASH" in holdings:
                cash = holdings.get("CASH", 0)
                current_values = {}
                total_portfolio_value = cash
                
                for t in self.tickers:
                    shares = holdings.get(t, 0)
                    if shares > 0.01 and t in d[-1]:
                        asset_value = shares * d[-1][t]["close"]
                        current_values[t] = asset_value
                        total_portfolio_value += asset_value
                
                if total_portfolio_value > 0:
                    new_allocation = {}
                    for t in self.active_positions:
                        # Lock existing positions to their exact current decimal weight to stop rebalancing
                        if holdings.get(t, 0) > 0.01 and t in current_values:
                            new_allocation[t] = current_values[t] / total_portfolio_value
                        else:
                            # Target a clean 50% slice of total account value for brand new entries
                            target_value = min(cash, total_portfolio_value * self.allocation_size)
                            new_allocation[t] = target_value / total_portfolio_value
                    return TargetAllocation(new_allocation)
            
            # SANDBOX FALLBACK: Standard split for the simulator when the 'CASH' key is absent
            new_allocation = {t: self.allocation_size for t in self.active_positions}
            return TargetAllocation(new_allocation)

        return None