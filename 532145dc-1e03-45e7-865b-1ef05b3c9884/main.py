from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import pandas_ta as ta


class TradingStrategy(Strategy):
    def __init__(self):
        self.tickers = ["TECL", "GDXU", "SOXL", "UCO", "AGQ"]

        self.clusters = {
            "TECL": "tech",
            "SOXL": "tech",
            "GDXU": "metals",
            "AGQ": "metals",
            "UCO": "energy",
        }

        self.max_positions = 3
        self.max_weight_per_position = 0.40
        self.min_cash_buffer = 0.05

        self.take_profit_pct = 0.10
        self.trailing_stop_pct = 0.08
        self.hard_stop_pct = 0.12
        self.max_hold_bars = 96

        self.rvol_threshold = 1.8
        self.vol_lookback = 20
        self.trend_lookback = 50
        self.vol_spike_ceiling = 1.5

        self.active_positions = {}
        self._logged_diagnostics = False

    @property
    def interval(self):
        return "5min"

    @property
    def assets(self):
        return self.tickers

    def _log_diagnostics_once(self, data):
        if self._logged_diagnostics:
            return
        ohlcv = data.get("ohlcv", [])
        sample_ticker = self.tickers[0]
        hist = [bar[sample_ticker] for bar in ohlcv if sample_ticker in bar]
        log(f"DIAGNOSTIC: bar buffer depth for {sample_ticker} = {len(hist)} bars")
        log(f"DIAGNOSTIC: raw holdings = {data.get('holdings')}")
        self._logged_diagnostics = True

    def _get_hist_df(self, ticker, ohlcv):
        rows = [bar[ticker] for bar in ohlcv if ticker in bar]
        if len(rows) < self.trend_lookback + 5:
            return None
        return pd.DataFrame(rows)

    def _trend_and_vol_ok(self, df):
        sma_trend = ta.sma(df["close"], length=self.trend_lookback)
        if sma_trend is None or pd.isna(sma_trend.iloc[-1]):
            return False, False
        trend_ok = df["close"].iloc[-1] > sma_trend.iloc[-1]

        returns = df["close"].pct_change().dropna()
        if len(returns) < self.vol_lookback * 4:
            return trend_ok, False

        recent_vol = returns.tail(self.vol_lookback).std()
        baseline_vol = returns.tail(self.vol_lookback * 4).std()
        vol_ok = baseline_vol > 0 and (recent_vol / baseline_vol) <= self.vol_spike_ceiling
        return trend_ok, vol_ok

    def _conviction_score(self, df):
        trend_ok, vol_ok = self._trend_and_vol_ok(df)
        if not (trend_ok and vol_ok):
            return 0, None

        vwma = ta.vwma(df["close"], df["volume"], length=12)
        macd = ta.macd(df["close"])
        vol_sma = ta.sma(df["volume"], length=20)

        if vwma is None or macd is None or vol_sma is None:
            return 0, None
        if pd.isna(vol_sma.iloc[-1]) or vol_sma.iloc[-1] == 0:
            return 0, None

        current_price = df["close"].iloc[-1]
        vwap_bullish = current_price > vwma.iloc[-1]
        macd_bullish = macd["MACD_12_26_9"].iloc[-1] > macd["MACDs_12_26_9"].iloc[-1]
        rvol = df["volume"].iloc[-1] / vol_sma.iloc[-1]

        if vwap_bullish and macd_bullish and rvol >= self.rvol_threshold:
            realized_vol = df["close"].pct_change().tail(self.vol_lookback).std()
            if realized_vol and realized_vol > 0:
                return rvol, realized_vol
        return 0, None

    def _latest_close(self, ticker, ohlcv):
        for row in reversed(ohlcv):
            if ticker in row:
                return row[ticker]["close"]
        return None

    def run(self, data):
        ohlcv = data.get("ohlcv")
        if not ohlcv:
            return TargetAllocation({})

        self._log_diagnostics_once(data)
        holdings = data.get("holdings", {}) or {}

        for t in self.tickers:
            held = holdings.get(t, 0)
            if held and held > 0.001 and t not in self.active_positions:
                cp = self._latest_close(t, ohlcv)
                if cp:
                    log(f"RESYNC: {t} held but untracked — resuming with proxy entry {cp}.")
                    self.active_positions[t] = {
                        "entry_price": cp,
                        "peak_price": cp,
                        "bars_held": 0,
                        "weight": self.max_weight_per_position,
                        "resynced": True,
                    }

        for t in list(self.active_positions.keys()):
            cp = self._latest_close(t, ohlcv)
            if cp is None:
                continue

            pos = self.active_positions[t]
            pos["bars_held"] += 1
            if cp > pos["peak_price"]:
                pos["peak_price"] = cp

            suppress_tp = pos.get("resynced") and pos["bars_held"] <= 1

            exit_reason = None
            if not suppress_tp and cp >= pos["entry_price"] * (1 + self.take_profit_pct):
                exit_reason = "TAKE PROFIT"
            elif cp <= pos["entry_price"] * (1 - self.hard_stop_pct):
                exit_reason = "HARD STOP"
            elif cp <= pos["peak_price"] * (1 - self.trailing_stop_pct):
                exit_reason = "TRAILING STOP"
            elif pos["bars_held"] >= self.max_hold_bars:
                exit_reason = "TIME STOP (stalled trade)"

            if exit_reason:
                log(f"{exit_reason}: {t} exit at {cp} | entry {pos['entry_price']} | held {pos['bars_held']} bars")
                del self.active_positions[t]
            elif pos.get("resynced"):
                pos["resynced"] = False

        active_clusters = {self.clusters[t] for t in self.active_positions}
        if len(self.active_positions) < self.max_positions:
            candidates = {}
            for t in self.tickers:
                if t in self.active_positions or self.clusters[t] in active_clusters:
                    continue
                df = self._get_hist_df(t, ohlcv)
                if df is None:
                    continue
                score, realized_vol = self._conviction_score(df)
                if score > 0:
                    candidates[t] = (score, realized_vol, df["close"].iloc[-1])

            if candidates:
                open_slots = self.max_positions - len(self.active_positions)
                ranked = sorted(candidates.items(), key=lambda kv: kv[1][0], reverse=True)[:open_slots]

                inv_vol = {t: 1.0 / v[1] for t, v in ranked}
                total_inv_vol = sum(inv_vol.values())

                used_weight = sum(p["weight"] for p in self.active_positions.values())
                remaining_capacity = 1.0 - self.min_cash_buffer - used_weight

                for t, (score, rv, price) in ranked:
                    if remaining_capacity <= 0.01 or total_inv_vol <= 0:
                        break
                    raw_weight = (inv_vol[t] / total_inv_vol) * remaining_capacity
                    weight = min(raw_weight, self.max_weight_per_position, remaining_capacity)
                    if weight < 0.05:
                        continue
                    self.active_positions[t] = {
                        "entry_price": price,
                        "peak_price": price,
                        "bars_held": 0,
                        "weight": weight,
                        "resynced": False,
                    }
                    active_clusters.add(self.clusters[t])
                    remaining_capacity -= weight
                    log(f"ENTRY: {t} | weight {weight:.2%} | RVOL {score:.2f} | cluster {self.clusters[t]}")

        allocation = {t: pos["weight"] for t, pos in self.active_positions.items()}
        return TargetAllocation(allocation)