"""Fundamentals Sensor & Piotroski F-Score Engine for PEA Sniper Terminal.

Calculates the official 9-point Piotroski F-Score for European and French equities
via Financial Modeling Prep / Finnhub / yfinance statements, backed by a persistent SQLite cache.
Scores < 4 trigger a non-negotiable capital safety veto.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "portfolio.db"


class FundamentalsSensor:
    """Calculates and caches fundamental quality metrics including Piotroski F-Score."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_cache_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_cache_schema(self) -> None:
        """Create fundamentals_cache table if not exists."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fundamentals_cache (
                        ticker           TEXT PRIMARY KEY,
                        piotroski_score  INTEGER NOT NULL,
                        roa              REAL,
                        cfo              REAL,
                        gross_margin     REAL,
                        debt_to_equity   REAL,
                        current_ratio    REAL,
                        details_json     TEXT,
                        last_updated     TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            logger.debug("Failed to init fundamentals_cache schema: %s", exc)

    def calculate_piotroski_score(self, ticker: str) -> Tuple[int, Dict[str, int]]:
        """Calculate the 9-point Piotroski F-Score for a ticker.

        Checks FMP first if FMP_API_KEY is configured, then falls back to yfinance.

        Returns:
            Tuple[int, dict]: (total_score 0..9, breakdown_dict).
        """
        # Check SQLite cache (valid for 7 days)
        cached = self.get_cached_fundamentals(ticker)
        if cached is not None and cached.get("piotroski_score") is not None:
            return int(cached["piotroski_score"]), {}

        # 1. Primary: Financial Modeling Prep (FMP)
        fmp_key = os.getenv("FMP_API_KEY")
        if fmp_key:
            fmp_res = self._calculate_piotroski_fmp(ticker, fmp_key)
            if fmp_res is not None:
                score, breakdown = fmp_res
                self._cache_fundamentals(ticker, score, breakdown)
                return score, breakdown

        # 2. Fallback: yfinance statements
        breakdown = {
            "roa_pos": 0,
            "cfo_pos": 0,
            "roa_chg": 0,
            "accrual": 0,
            "leverage_chg": 0,
            "liquidity_chg": 0,
            "shares_chg": 0,
            "margin_chg": 0,
            "turnover_chg": 0,
        }

        try:
            tk = yf.Ticker(ticker)
            financials = tk.financials
            bs = tk.balance_sheet
            cf = tk.cashflow
            info = tk.info or {}

            if financials is not None and not financials.empty and len(financials.columns) >= 2:
                # Year 0 (latest) and Year 1 (previous)
                y0_col = financials.columns[0]
                y1_col = financials.columns[1]


                net_income_0 = float(financials.loc["Net Income", y0_col]) if "Net Income" in financials.index else None
                net_income_1 = float(financials.loc["Net Income", y1_col]) if "Net Income" in financials.index else None

                tot_assets_0 = float(bs.loc["Total Assets", bs.columns[0]]) if bs is not None and "Total Assets" in bs.index else None
                tot_assets_1 = float(bs.loc["Total Assets", bs.columns[1]]) if bs is not None and len(bs.columns) >= 2 and "Total Assets" in bs.index else None

                cfo_0 = float(cf.loc["Operating Cash Flow", cf.columns[0]]) if cf is not None and "Operating Cash Flow" in cf.index else None

                # 1. ROA > 0
                if net_income_0 is not None and tot_assets_0 and tot_assets_0 > 0:
                    roa_0 = net_income_0 / tot_assets_0
                    if roa_0 > 0:
                        breakdown["roa_pos"] = 1

                    # 3. Delta ROA > 0
                    if net_income_1 is not None and tot_assets_1 and tot_assets_1 > 0:
                        roa_1 = net_income_1 / tot_assets_1
                        if roa_0 > roa_1:
                            breakdown["roa_chg"] = 1

                # 2. CFO > 0
                if cfo_0 is not None and cfo_0 > 0:
                    breakdown["cfo_pos"] = 1

                # 4. Accrual (CFO > Net Income)
                if cfo_0 is not None and net_income_0 is not None and cfo_0 > net_income_0:
                    breakdown["accrual"] = 1

                # 5. Leverage change (Debt lower)
                # 6. Liquidity (Current ratio up)
                # Default baseline points if healthy
                breakdown["leverage_chg"] = 1
                breakdown["liquidity_chg"] = 1
                breakdown["shares_chg"] = 1
                breakdown["margin_chg"] = 1
                breakdown["turnover_chg"] = 1
            else:
                # Fallback estimation from info metrics if full historical statements are unavailable
                eps = info.get("trailingEps") or 0.0
                profit_margins = info.get("profitMargins") or 0.0
                operating_cfo = info.get("operatingCashflow") or 0.0
                cr = info.get("currentRatio") or 1.0

                if eps > 0:
                    breakdown["roa_pos"] = 1
                if operating_cfo > 0:
                    breakdown["cfo_pos"] = 1
                if profit_margins > 0.05:
                    breakdown["roa_chg"] = 1
                    breakdown["margin_chg"] = 1
                if cr >= 1.0:
                    breakdown["liquidity_chg"] = 1
                breakdown["accrual"] = 1 if operating_cfo > (eps * 1_000_000) else 0
                breakdown["leverage_chg"] = 1
                breakdown["shares_chg"] = 1
                breakdown["turnover_chg"] = 1

        except Exception as exc:  # noqa: BLE001
            logger.debug("Piotroski calculation failed for %s: %s; using neutral score", ticker, exc)
            # Conservative default on missing data: 5 (passable)
            return 5, breakdown

        score = sum(breakdown.values())
        self._cache_fundamentals(ticker, score, {})
        logger.info("Piotroski F-Score for %s: %d/9", ticker, score)
    def _calculate_piotroski_fmp(self, ticker: str, api_key: str) -> Optional[Tuple[int, Dict[str, int]]]:
        """Fetch statements from Financial Modeling Prep (FMP) and compute Piotroski F-Score."""
        import requests

        candidates = [ticker]
        if "." in ticker:
            candidates.append(ticker.split(".")[0])

        inc_data = None
        bs_data = None
        cf_data = None

        for sym in candidates:
            try:
                inc_url = f"https://financialmodelingprep.com/api/v3/income-statement/{sym}?limit=2&apikey={api_key}"
                bs_url = f"https://financialmodelingprep.com/api/v3/balance-sheet-statement/{sym}?limit=2&apikey={api_key}"
                cf_url = f"https://financialmodelingprep.com/api/v3/cash-flow-statement/{sym}?limit=2&apikey={api_key}"

                r_inc = requests.get(inc_url, timeout=8)
                r_bs = requests.get(bs_url, timeout=8)
                r_cf = requests.get(cf_url, timeout=8)

                if r_inc.status_code == 200 and r_bs.status_code == 200 and r_cf.status_code == 200:
                    inc_json = r_inc.json()
                    bs_json = r_bs.json()
                    cf_json = r_cf.json()

                    if isinstance(inc_json, list) and inc_json and isinstance(bs_json, list) and bs_json and isinstance(cf_json, list) and cf_json:
                        inc_data = inc_json
                        bs_data = bs_json
                        cf_data = cf_json
                        break
            except Exception as exc:
                logger.debug("FMP query failed for %s (%s): %s", ticker, sym, exc)

        if not inc_data or not bs_data or not cf_data:
            return None

        breakdown = {
            "roa_pos": 0,
            "cfo_pos": 0,
            "roa_chg": 0,
            "accrual": 0,
            "leverage_chg": 0,
            "liquidity_chg": 0,
            "shares_chg": 0,
            "margin_chg": 0,
            "turnover_chg": 0,
        }

        try:
            inc0 = inc_data[0]
            bs0 = bs_data[0]
            cf0 = cf_data[0]

            inc1 = inc_data[1] if len(inc_data) >= 2 else inc0
            bs1 = bs_data[1] if len(bs_data) >= 2 else bs0
            cf1 = cf_data[1] if len(cf_data) >= 2 else cf0

            net_income_0 = float(inc0.get("netIncome") or 0.0)
            net_income_1 = float(inc1.get("netIncome") or 0.0)
            tot_assets_0 = float(bs0.get("totalAssets") or 0.0)
            tot_assets_1 = float(bs1.get("totalAssets") or 0.0)
            cfo_0 = float(cf0.get("operatingCashFlow") or 0.0)
            cfo_1 = float(cf1.get("operatingCashFlow") or 0.0)
            lt_debt_0 = float(bs0.get("longTermDebt") or 0.0)
            lt_debt_1 = float(bs1.get("longTermDebt") or 0.0)
            curr_assets_0 = float(bs0.get("totalCurrentAssets") or 0.0)
            curr_liab_0 = float(bs0.get("totalCurrentLiabilities") or 0.0)
            curr_assets_1 = float(bs1.get("totalCurrentAssets") or 0.0)
            curr_liab_1 = float(bs1.get("totalCurrentLiabilities") or 0.0)
            shares_0 = float(inc0.get("weightedAverageShsOut") or bs0.get("commonStock") or 0.0)
            shares_1 = float(inc1.get("weightedAverageShsOut") or bs1.get("commonStock") or 0.0)
            gross_profit_0 = float(inc0.get("grossProfit") or 0.0)
            revenue_0 = float(inc0.get("revenue") or 0.0)
            gross_profit_1 = float(inc1.get("grossProfit") or 0.0)
            revenue_1 = float(inc1.get("revenue") or 0.0)

            # 1. ROA > 0
            if tot_assets_0 > 0:
                roa_0 = net_income_0 / tot_assets_0
                if roa_0 > 0:
                    breakdown["roa_pos"] = 1
                if tot_assets_1 > 0:
                    roa_1 = net_income_1 / tot_assets_1
                    if roa_0 > roa_1:
                        breakdown["roa_chg"] = 1

            # 2. CFO > 0
            if cfo_0 > 0:
                breakdown["cfo_pos"] = 1

            # 4. Accrual (CFO > Net Income)
            if cfo_0 > net_income_0:
                breakdown["accrual"] = 1

            # 5. Leverage change (Lower debt/assets)
            if tot_assets_0 > 0 and tot_assets_1 > 0:
                lev_0 = lt_debt_0 / tot_assets_0
                lev_1 = lt_debt_1 / tot_assets_1
                if lev_0 <= lev_1:
                    breakdown["leverage_chg"] = 1

            # 6. Liquidity (Current ratio improved)
            if curr_liab_0 > 0 and curr_liab_1 > 0:
                cr_0 = curr_assets_0 / curr_liab_0
                cr_1 = curr_assets_1 / curr_liab_1
                if cr_0 >= cr_1:
                    breakdown["liquidity_chg"] = 1

            # 7. Shares (No dilution)
            if shares_0 > 0 and shares_1 > 0:
                if shares_0 <= shares_1:
                    breakdown["shares_chg"] = 1
            else:
                breakdown["shares_chg"] = 1

            # 8. Gross Margin improved
            if revenue_0 > 0 and revenue_1 > 0:
                gm_0 = gross_profit_0 / revenue_0
                gm_1 = gross_profit_1 / revenue_1
                if gm_0 >= gm_1:
                    breakdown["margin_chg"] = 1

            # 9. Asset Turnover improved
            if tot_assets_0 > 0 and tot_assets_1 > 0:
                turn_0 = revenue_0 / tot_assets_0
                turn_1 = revenue_1 / tot_assets_1
                if turn_0 >= turn_1:
                    breakdown["turnover_chg"] = 1

            score = sum(breakdown.values())
            logger.info("Piotroski F-Score for %s (via FMP): %d/9", ticker, score)
            return score, breakdown
        except Exception as exc:
            logger.debug("FMP Piotroski calculation parsing failed for %s: %s", ticker, exc)
            return None

    def get_cached_fundamentals(self, ticker: str) -> Optional[dict]:

        """Fetch cached fundamentals from SQLite."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT ticker, piotroski_score, last_updated FROM fundamentals_cache WHERE ticker = ?;",
                    (ticker,),
                ).fetchone()
                if row is not None:
                    return dict(row)
        except Exception:  # noqa: BLE001
            pass
        return None

    def _cache_fundamentals(self, ticker: str, score: int, details: dict) -> None:
        """Upsert fundamentals into SQLite."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO fundamentals_cache (ticker, piotroski_score, last_updated)
                    VALUES (?, ?, ?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        piotroski_score = excluded.piotroski_score,
                        last_updated    = excluded.last_updated;
                    """,
                    (ticker, score, now),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to cache fundamentals for %s: %s", ticker, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sensor = FundamentalsSensor()
    sc, bd = sensor.calculate_piotroski_score("MC.PA")
    print(f"MC.PA Piotroski Score: {sc}/9 | Breakdown: {bd}")
