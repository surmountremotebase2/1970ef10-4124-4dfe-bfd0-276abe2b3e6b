from surmount.base_class import Strategy, TargetAllocation
from surmount.logging import log
import pandas as pd
import numpy as np

class TradingStrategy(Strategy):
    @property
    def interval(self):
        # 1-hour interval isolates the 9:30 AM - 10:30 AM opening bar 
        # to calculate the volume Z-score gap defense.
        return "1hour"

    @property
    def assets(self):
        # The clean macro-vector core target list and cash vehicle.
        return ["SOXL", "TECL", "AGQ", "UCO", "GDXU", "SHV"] 

    @property
    def data(self):
        return []

    def run(self, data):
        # 1. Initialize Baseline - Default to Cash Equivalent (SHV)
        assets = [a for a in self.assets if a != "SHV"]
        allocation = {a: 0.0 for a in self.assets}
        
        ohlcv = data.get("ohlcv", [])
        if len(ohlcv) < 140: # Require historical 1-hour bars for rolling windows 
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        # 2. Reconstruct Dataframes from Surmount Data Arrays
        close_prices = {}
        volumes = {}
        for asset in assets:
            closes = []
            vols = []
            for row in ohlcv:
                if asset in row:
                    closes.append(row[asset].get('close', 0))
                    vols.append(row[asset].get('volume', 0))
            if closes:
                close_prices[asset] = pd.Series(closes)
                volumes[asset] = pd.Series(vols)
        
        if not close_prices:
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        prices_df = pd.DataFrame(close_prices)
        returns_df = prices_df.pct_change().dropna()

        if len(returns_df) < 70:
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        # 3. Calculate the Convexity Premium (Cp)
        # 10 trading days * ~7 hours/day = 70 periods
        rolling_mean = returns_df.rolling(window=70).mean()
        rolling_var = returns_df.rolling(window=70).var()
        
        current_cp = (rolling_mean.iloc[-1] / (rolling_var.iloc[-1] + 1e-8))
        
        # 4. Asymmetric Opening Filter (Volume Z-Score)
        valid_assets = []
        for asset in assets:
            # Only evaluate if directional compounding outpaces variance decay
            if current_cp[asset] > 1.2:
                vol_series = volumes[asset]
                if len(vol_series) > 140:
                    current_vol = vol_series.iloc[-1]
                    vol_mean_20d = vol_series.iloc[-140:].mean()
                    vol_std_20d = vol_series.iloc[-140:].std()
                    
                    z_score = (current_vol - vol_mean_20d) / (vol_std_20d + 1e-8)
                    
                    # Require institutional volume consensus (Z > 1.5) to unlock trade execution
                    if z_score > 1.5:
                        valid_assets.append(asset)
        
        # 5. Inverse Covariance Risk Parity Layer
        if not valid_assets:
            # Capital is automatically sidelined to cash if conditions are unmet
            allocation["SHV"] = 1.0
            return TargetAllocation(allocation)

        # Calculate 20-day (140 period) covariance matrix for valid assets
        cov_matrix = returns_df[valid_assets].tail(140).cov()
        
        # Calculate inverse volatility weights
        inv_vol = 1.0 / np.sqrt(np.diag(cov_matrix) + 1e-8)
        risk_parity_weights = inv_vol / np.sum(inv_vol)
        
        # 6. Self-Preservation Logic (Dynamic Volatility Halt)
        # Calculates real-time annualized volatility of the aggregate active portfolio
        port_variance = np.dot(risk_parity_weights.T, np.dot(cov_matrix, risk_parity_weights))
        port_vol_annualized = np.sqrt(port_variance) * np.sqrt(252 * 7) 
        
        if port_vol_annualized > 0.25:
            # Enforce an automatic 50% risk exposure reduction if macro volatility spikes
            risk_parity_weights *= 0.5

        # 7. Final Allocation Mapping
        for idx, asset in enumerate(valid_assets):
            allocation[asset] = round(float(risk_parity_weights[idx]), 4)
            
        # Remainder of total capital defaults cleanly to SHV
        allocation["SHV"] = round(1.0 - sum([allocation[a] for a in valid_assets]), 4)

        return TargetAllocation(allocation)