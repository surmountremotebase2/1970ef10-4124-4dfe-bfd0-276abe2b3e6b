from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    def __init__(self):
        # Original 5-Ticker Macro Roster
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]
       
        # Dual-Bullet Parameters
        self.allocation_size = 0.50 # 50% per trade
        self.max_positions = 2      # Maximum of 2 concurrent bullets
        self.vwap_len = 12
        self.rvol_threshold = 1.8
        self.trailing_stop_pct = 0.08
        self.take_profit_pct = 0.10
       
        # Upgraded Internal Memory Tracker
        # Format: {"TICKER": {"entry_price": X, "peak_price": Y}}
        self.active_positions = {}
        self.exited_tickers = [] # Circuit breaker to handle backtester settlement lag

    @property
    def interval(self): return "5min"

    @property
    def assets(self): return self.tickers

    def get_conviction_score(self, history):
        if len(history) < 200: return 0
        df = pd.DataFrame(history)
       
        recent_df = df.tail(12)
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
       
        holdings = data.get("holdings", {})
       
        # --- AMNESIA RECOVERY CIRCUIT BREAKER (Dual-Bullet Upgraded) ---
        if holdings:
            for t in self.tickers:
                if holdings.get(t, 0) > 0 and t not in self.active_positions:
                    if t not in self.exited_tickers and len(self.active_positions) < self.max_positions:
                        # Re-sync a missing live position
                        cp = d[-1][t]["close"] if t in d[-1] else 0
                        self.active_positions[t] = {"entry_price": cp, "peak_price": cp}
                        log(f"AMNESIA RECOVERY: Resynced 50% live position for {t}")

        # Clear the lag circuit breaker at the start of a new bar
        self.exited_tickers = []
        state_changed = False

        # --- 1. SWING MANAGEMENT (Manage held positions independently) ---
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
                del self.active_positions[t]
                state_changed = True
                continue

            # DEFENSIVE EXIT: 8% Trailing Stop
            if cp <= metrics["peak_price"] * (1 - self.trailing_stop_pct):
                log(f"SWING STOP: {t} exit at {cp}.")
                self.exited_tickers.append(t)
                del self.active_positions[t]
                state_changed = True
                continue

        # --- 2. PREDATORY SELECTION (Deploy available cash reserve) ---
        if len(self.active_positions) < self.max_positions:
            scores = {}
            for t in self.tickers:
                # The Sieve: Prevent buying a ticker we already hold
                if t in self.active_positions:
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

        # --- 3. ALLOCATION EXECUTION ---
        # If an entry or exit occurred, build a new allocation dictionary mapping all active trades to 0.50
        if state_changed:
            new_allocation = {t: self.allocation_size for t in self.active_positions}
            return TargetAllocation(new_allocation)

        return None