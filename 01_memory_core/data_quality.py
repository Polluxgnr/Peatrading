"""Data Quality Gateway & Pipeline Hardening for PEA Pollux.

Enforces institutional data quality standards before any market tick or OHLCV bar
is committed to DuckDB or processed by quantitative models:
  - Missing value forward-filling (capped at strict maximum 3 consecutive sessions).
  - Stale data detection and eviction.
  - Outlier detection (daily returns > +/- 40% or rolling return Z-score >= 4.0 sigma).
  - Schema normalization and contract validation.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("data_quality")

_REQUIRED_COLS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]


class DataQualityGateway:
    """Quality gate validating and cleaning OHLCV batches before persistence."""

    def __init__(
        self,
        max_ffill_limit: int = 3,
        outlier_return_threshold: float = 0.40,
        outlier_zscore_threshold: float = 4.0,
    ) -> None:
        self.max_ffill_limit = max_ffill_limit
        self.outlier_return_threshold = outlier_return_threshold
        self.outlier_zscore_threshold = outlier_zscore_threshold

    def validate_ohlcv_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate, clean, forward-fill, and tag outliers on incoming OHLCV bars.

        Args:
            df: Input DataFrame containing OHLCV columns.

        Returns:
            pd.DataFrame: Cleaned DataFrame with ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'is_outlier'].
                          Returns empty DataFrame if data is completely invalid.
        """
        if df is None or df.empty:
            logger.warning("DataQualityGateway received empty or None DataFrame.")
            return pd.DataFrame(columns=_REQUIRED_COLS + ["is_outlier"])

        clean = df.copy()

        # Map lowercase or alternate column names to Canonical PascalCase
        col_map = {}
        for c in clean.columns:
            cl = str(c).strip().lower()
            if cl == "ticker":
                col_map[c] = "Ticker"
            elif cl in ("date", "timestamp", "datetime", "ts"):
                col_map[c] = "Date"
            elif cl == "open":
                col_map[c] = "Open"
            elif cl == "high":
                col_map[c] = "High"
            elif cl == "low":
                col_map[c] = "Low"
            elif cl == "close":
                col_map[c] = "Close"
            elif cl in ("volume", "vol"):
                col_map[c] = "Volume"

        clean = clean.rename(columns=col_map)

        # Ensure index date is preserved if Date was index
        if "Date" not in clean.columns and isinstance(clean.index, (pd.DatetimeIndex, pd.PeriodIndex)):
            clean["Date"] = clean.index

        # Check for missing required columns
        missing = [c for c in _REQUIRED_COLS if c not in clean.columns]
        if missing:
            logger.error("DataQualityGateway: Batch missing mandatory columns: %s", missing)
            raise ValueError(f"Batch missing mandatory columns: {missing}")

        clean = clean[_REQUIRED_COLS].copy()

        # Ensure Date is normalized date objects or strings
        clean["Date"] = pd.to_datetime(clean["Date"]).dt.date

        # Sort chronologically by ticker and date
        clean = clean.sort_values(by=["Ticker", "Date"]).reset_index(drop=True)

        # 1. Forward-fill missing values with strict limit=3
        grouped = clean.groupby("Ticker", group_keys=False)
        
        numeric_cols = ["Open", "High", "Low", "Close", "Volume"]
        for col in numeric_cols:
            clean[col] = pd.to_numeric(clean[col], errors="coerce")

        # Forward fill up to max_ffill_limit
        clean[["Open", "High", "Low", "Close", "Volume"]] = grouped[numeric_cols].apply(
            lambda g: g.ffill(limit=self.max_ffill_limit)
        )

        # Drop rows where Close is still null (e.g. missing for > 3 days or initial bars)
        initial_len = len(clean)
        clean = clean.dropna(subset=["Close"]).reset_index(drop=True)
        dropped_count = initial_len - len(clean)
        if dropped_count > 0:
            logger.warning(
                "DataQualityGateway: Dropped %d stale/unfillable rows with missing Close prices.",
                dropped_count,
            )

        if clean.empty:
            return pd.DataFrame(columns=_REQUIRED_COLS + ["is_outlier"])

        # Fill any remaining Open/High/Low with Close price, and Volume with 0
        clean["Open"] = clean["Open"].fillna(clean["Close"])
        clean["High"] = clean["High"].fillna(clean["Close"])
        clean["Low"] = clean["Low"].fillna(clean["Close"])
        clean["Volume"] = clean["Volume"].fillna(0.0).astype(int)

        # 2. Outlier Detection
        # Calculate daily return per ticker
        clean["is_outlier"] = False

        for ticker, grp in clean.groupby("Ticker"):
            if len(grp) < 2:
                continue
            
            c_prices = grp["Close"]
            rets = c_prices.pct_change()

            # Extreme percentage jump/drop > 40%
            is_extreme = rets.abs() > self.outlier_return_threshold

            # Rolling return Z-score
            if len(grp) >= 20:
                roll_mean = rets.rolling(20, min_periods=5).mean()
                roll_std = rets.rolling(20, min_periods=5).std().replace(0, np.nan)
                zscores = (rets - roll_mean).abs() / roll_std
                is_z_outlier = zscores >= self.outlier_zscore_threshold
                is_flagged = is_extreme | is_z_outlier.fillna(False)
            else:
                is_flagged = is_extreme

            if is_flagged.any():
                flagged_idx = grp[is_flagged].index
                clean.loc[flagged_idx, "is_outlier"] = True
                logger.warning(
                    "DataQualityGateway: Flagged %d price return outlier(s) for ticker %s.",
                    len(flagged_idx), ticker,
                )

        return clean[_REQUIRED_COLS + ["is_outlier"]]
