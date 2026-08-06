class RiskEngine:
    """Dynamic Risk Management and Position Sizing Engine."""
    
    @staticmethod
    def calculate_atr_stop(current_price: float, atr_14: float, multiplier: float = 2.5) -> float:
        """
        Calculate a dynamic stop-loss level based on Average True Range (ATR).
        
        Args:
            current_price: The current entry price of the asset.
            atr_14: The 14-day Average True Range.
            multiplier: The ATR multiplier (default 2.5).
            
        Returns:
            The calculated stop-loss price level.
        """
        if current_price <= 0 or atr_14 <= 0:
            return 0.0
        return max(0.0, current_price - (multiplier * atr_14))
        
    @staticmethod
    def calculate_volatility_parity_weight(asset_volatility: float, target_volatility: float = 0.20, max_weight: float = 0.15) -> float:
        """
        Calculate the maximum portfolio weight for an asset based on volatility parity.
        More volatile assets get smaller weights to equalize risk contribution.
        
        Args:
            asset_volatility: The annualized volatility (e.g., standard deviation of returns).
            target_volatility: The target portfolio volatility (default 20%).
            max_weight: The absolute maximum weight allowed for any single position (default 15%).
            
        Returns:
            The recommended allocation weight as a float (e.g., 0.12 for 12%).
        """
        if asset_volatility <= 0:
            return max_weight
            
        # Volatility scaling: target / asset_volatility
        raw_weight = target_volatility / asset_volatility
        
        # We also scale it down by some constant factor depending on the sizing model,
        # but for simple parity we just cap it at max_weight.
        # Typically, weight = (Target Vol) / (Asset Vol) / N_assets.
        # For an individual position sizing, we return min(raw_weight * scaling, max_weight).
        # We will use raw_weight * 0.10 as a base sizing heuristic (assuming ~10 positions target)
        adjusted_weight = raw_weight * 0.10
        
        return min(adjusted_weight, max_weight)
