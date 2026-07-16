from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd

class TradingStrategy(Strategy):
    def __init__(self):
        # Explicitly segregated sleeves
        self.tech_tickers = ["TECL", "SOXL"]
        self.commodity_tickers = ["GDXU", "UCO", "AGQ"]
        
        # Combined roster for data retrieval
        self.tickers = self.tech_tickers + self.commodity_tickers
        self.macro_proxy = "QQQ"
        
        # Risk & Core Parameters (Per Sleeve)
        self.allocation_size = 0.50
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
        
        # Internal State Trackers per sleeve
        self.active_tech = None # Stores string ticker or None
        self.active_commodity = None # Stores string ticker or None
        self.position_metrics = {} # Tracks tracking metadata

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers + [self.macro_proxy]

    def check_macro_environment(self, history):
        """ The Macro Master Switch: Evaluates broad market health """
        if len(history) < 200: return False
        df = pd.DataFrame(history)
        
        current_price = df['close'].iloc[-1]
        sma_macro = df['close'].tail(200).mean()
        
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_bullish = macd_line.iloc[-1] > signal_line.iloc[-1]
        
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
        
        raw_holdings = data.get("holdings", {})
        holdings = {str(k).upper(): v for k, v in raw_holdings.items()}
        
        state_changed = False

        # --- PHASE 1: SWING MANAGEMENT (Exits) ---
        active_list = []
        if self.active_tech: active_list.append(self.active_tech)
        if self.active_commodity: active_list.append(self.active_commodity)

        for t in active_list:
            if t not in d[-1]: continue
            
            cp = d[-1][t]["close"]
            metrics = self.position_metrics[t]
            
            if cp > metrics["peak_price"]:
                self.position_metrics[t]["peak_price"] = cp
            
            # Take Profit Exit
            if cp >= metrics["entry_price"] * (1 + self.take_profit_pct):
                log(f"TAKE PROFIT: {t} exit at {cp}.")
                if t in self.tech_tickers: self.active_tech = None
                else: self.active_commodity = None
                del self.position_metrics[t]
                state_changed = True
                continue

            # Trailing Stop Exit
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                if t in self.tech_tickers: self.active_tech = None
                else: self.active_commodity = None
                del self.position_metrics[t]
                state_changed = True
                continue

        # --- PHASE 2: THE MACRO MASTER SWITCH ---
        macro_safe = False
        qqq_hist = [bar[self.macro_proxy] for bar in d if self.macro_proxy in bar]
        if len(qqq_hist) > 0:
            macro_safe = self.check_macro_environment(qqq_hist)

        # --- PHASE 3: COMPARTMENTALIZED ENTRY SELECTION ---
        
        # Tech Chamber (Sleeve 1)
        if self.active_tech is None and macro_safe:
            tech_scores = {}
            for t in self.tech_tickers:
                if holdings.get(t, 0) > 0.01: continue
                hist = [bar[t] for bar in d if t in bar]
                if len(hist) > 0:
                    score = self.get_conviction_score(hist)
                    if score > 0: tech_scores[t] = score
            
            if tech_scores:
                best_tech = max(tech_scores, key=tech_scores.get)
                self.active_tech = best_tech
                self.position_metrics[best_tech] = {
                    "entry_price": d[-1][best_tech]["close"],
                    "peak_price": d[-1][best_tech]["close"]
                }
                log(f"TECH SLEEVE ENTRY (50%): {best_tech} | RVOL: {tech_scores[best_tech]:.2f}")
                state_changed = True

        # Commodity Chamber (Sleeve 2)
        if self.active_commodity is None:
            comm_scores = {}
            for t in self.commodity_tickers:
                if holdings.get(t, 0) > 0.01: continue
                hist = [bar[t] for bar in d if t in bar]
                if len(hist) > 0:
                    score = self.get_conviction_score(hist)
                    if score > 0: comm_scores[t] = score
            
            if comm_scores:
                best_comm = max(comm_scores, key=comm_scores.get)
                self.active_commodity = best_comm
                self.position_metrics[best_comm] = {
                    "entry_price": d[-1][best_comm]["close"],
                    "peak_price": d[-1][best_comm]["close"]
                }
                log(f"COMMODITY SLEEVE ENTRY (50%): {best_comm} | RVOL: {comm_scores[best_comm]:.2f}")
                state_changed = True

        # --- PHASE 4: NATIVE ALLOCATION ---
        if state_changed:
            alloc = {}
            cash = holdings.get("CASH", 0)
            
            total_equity = cash
            for ticker, shares in holdings.items():
                if ticker in self.tickers and ticker in d[-1] and shares > 0.01:
                    total_equity += shares * d[-1][ticker]["close"]
            
            current_actives = []
            if self.active_tech: current_actives.append(self.active_tech)
            if self.active_commodity: current_actives.append(self.active_commodity)

            for t in current_actives:
                if holdings.get(t, 0) > 0.01 and total_equity > 0 and t in d[-1]:
                    current_weight = (holdings[t] * d[-1][t]["close"]) / total_equity
                    alloc[t] = current_weight
                else:
                    alloc[t] = self.allocation_size
                    
            return TargetAllocation(alloc)

        return None