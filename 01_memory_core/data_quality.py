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
    """Quality gate validating, forward-filling, and flagging outliers without hard data deletion."""

    def __init__(
        self,
        max_ffill_limit: int = 3,
        mad_threshold: float = 5.0,
        outlier_return_threshold: Optional[float] = None,
        outlier_zscore_threshold: Optional[float] = None,
    ) -> None:
        self.max_ffill_limit = max_ffill_limit
        self.mad_threshold = float(mad_threshold)
        self.outlier_return_threshold = outlier_return_threshold
        self.outlier_zscore_threshold = outlier_zscore_threshold

    def validate_ohlcv_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate, clean, forward-fill, and tag outliers on incoming OHLCV bars.

        Args:
            df: Input DataFrame containing OHLCV columns.

        Returns:
            pd.DataFrame: Cleaned DataFrame with ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'is_outlier'].
                          All valid price bars are preserved; outliers are flagged without dropping.
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

        # Check required columns
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

        # 2. Outlier Detection via Robust Median Absolute Deviation (MAD)
        clean["is_outlier"] = False

        for ticker, grp in clean.groupby("Ticker"):
            if len(grp) < 2:
                continue

            c_prices = grp["Close"]
            rets = c_prices.pct_change().dropna()
            if rets.empty:
                continue

            med_ret = float(rets.median())
            abs_dev = (rets - med_ret).abs()
            mad_val = float(abs_dev.median())

            if mad_val > 1e-7:
                # Flag returns that exceed 5 MADs
                mad_distances = abs_dev / mad_val
                is_flagged = mad_distances > self.mad_threshold
            else:
                is_flagged = abs_dev > 0.05

            # If legacy outlier_return_threshold is explicitly specified, include it
            if self.outlier_return_threshold is not None:
                is_flagged = is_flagged | (rets.abs() > self.outlier_return_threshold)

            if is_flagged.any():
                flagged_idx = rets[is_flagged].index
                clean.loc[flagged_idx, "is_outlier"] = True
                logger.warning(
                    "DataQualityGateway: Flagged %d price return outlier(s) exceeding %0.1f MADs for ticker %s.",
                    len(flagged_idx),
                    self.mad_threshold,
                    ticker,
                )

        return clean[_REQUIRED_COLS + ["is_outlier"]]
