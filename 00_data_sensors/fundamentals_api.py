"""Fundamental data sensor with Finnhub primary + yfinance fallback.

Designed for graceful degradation:
- Finnhub key missing/rate-limited/network error -> fallback to yfinance
- Any parsing issue returns partial/empty metrics, never raises to callers
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

try:
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        out = float(value)
        return out if out == out else None
    except (TypeError, ValueError):
        return None


class FundamentalsSensor:
    """Fetches basic value/quality factors for EU tickers."""

    def __init__(self) -> None:
        self.api_key = (os.getenv("FINNHUB_API_KEY") or "").strip()
        self._session = requests.Session()

    @staticmethod
    def _map_symbol(ticker: str) -> str:
        """Map Yahoo symbol to Finnhub symbol format.

        Most EU Yahoo symbols (e.g. MC.PA, OR.PA, ASML.AS) are accepted as-is.
        """
        return str(ticker or "").strip().upper()

    def _from_finnhub(self, ticker: str) -> dict:
        blank = {
            "pe_ratio": None,
            "pb_ratio": None,
            "roe": None,
            "debt_to_equity": None,
            "source": "none",
        }
        if not self.api_key:
            return blank

        symbol = self._map_symbol(ticker)
        url = "https://finnhub.io/api/v1/stock/metric"
        try:
            resp = self._session.get(
                url,
                params={
                    "symbol": symbol,
                    "metric": "all",
                    "token": self.api_key,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug("Finnhub HTTP %s for %s", resp.status_code, symbol)
                return blank
            payload = resp.json()
            metric = payload.get("metric") if isinstance(payload, dict) else None
            if not isinstance(metric, dict):
                return blank

            pe = _to_float(metric.get("peExclExtraTTM"))
            pb = _to_float(metric.get("pbAnnual"))
            roe = _to_float(metric.get("roeTTM"))
            debt = _to_float(metric.get("totalDebt/totalEquityAnnual"))
            # Some endpoints expose debt/equity as percent points.
            if debt is not None and debt > 50:
                debt = debt / 100.0

            out = {
                "pe_ratio": pe,
                "pb_ratio": pb,
                "roe": roe,
                "debt_to_equity": debt,
                "source": "finnhub",
            }
            if any(v is not None for k, v in out.items() if k != "source"):
                return out
            return blank
        except Exception as exc:  # noqa: BLE001
            logger.debug("Finnhub metrics failed for %s: %s", symbol, exc)
            return blank

    @staticmethod
    def _from_yfinance(ticker: str) -> dict:
        blank = {
            "pe_ratio": None,
            "pb_ratio": None,
            "roe": None,
            "debt_to_equity": None,
            "source": "none",
        }
        if yf is None:
            return blank
        try:
            info = yf.Ticker(ticker).info or {}
            if not isinstance(info, dict) or not info:
                return blank
            pe = _to_float(info.get("trailingPE"))
            pb = _to_float(info.get("priceToBook"))
            roe = _to_float(info.get("returnOnEquity"))
            debt = _to_float(info.get("debtToEquity"))
            # yfinance debtToEquity often comes as percent points.
            if debt is not None and debt > 50:
                debt = debt / 100.0
            out = {
                "pe_ratio": pe,
                "pb_ratio": pb,
                "roe": roe,
                "debt_to_equity": debt,
                "source": "yfinance",
            }
            if any(v is not None for k, v in out.items() if k != "source"):
                return out
            return blank
        except Exception as exc:  # noqa: BLE001
            logger.debug("yfinance fundamentals failed for %s: %s", ticker, exc)
            return blank

    def _from_alphavantage(self, ticker: str) -> dict:
        blank = {
            "pe_ratio": None, "pb_ratio": None, "roe": None,
            "debt_to_equity": None, "source": "none",
        }
        av_key = (os.getenv("ALPHAVANTAGE_API_KEY") or "").strip()
        if not av_key:
            return blank
        symbol = self._map_symbol(ticker)
        try:
            resp = self._session.get(
                "https://www.alphavantage.co/query",
                params={"function": "OVERVIEW", "symbol": symbol, "apikey": av_key},
                timeout=12,
            )
            if resp.status_code != 200:
                return blank
            data = resp.json()
            if not isinstance(data, dict) or "Symbol" not in data:
                return blank
            pe = _to_float(data.get("TrailingPE"))
            pb = _to_float(data.get("PriceToBookRatio"))
            roe = _to_float(data.get("ReturnOnEquityTTM"))
            debt = _to_float(data.get("DebtToEquity"))
            if debt is not None and debt > 50:
                debt = debt / 100.0
            out = {
                "pe_ratio": pe, "pb_ratio": pb, "roe": roe,
                "debt_to_equity": debt, "source": "alphavantage",
            }
            if any(v is not None for k, v in out.items() if k != "source"):
                return out
            return blank
        except Exception as exc:  # noqa: BLE001
            logger.debug("Alpha Vantage failed for %s: %s", ticker, exc)
            return blank

    def _from_fmp(self, ticker: str) -> dict:
        blank = {"piotroski_score": None, "source": "none"}
        fmp_key = (os.getenv("FMP_API_KEY") or "").strip()
        if not fmp_key:
            return blank
        # FMP expects US-style symbols; strip .PA/.AS suffix as best-effort.
        symbol = ticker.split(".")[0]
        try:
            resp = self._session.get(
                "https://financialmodelingprep.com/api/v4/score",
                params={"symbol": symbol, "apikey": fmp_key},
                timeout=12,
            )
            if resp.status_code != 200:
                return blank
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                score = _to_float(data[0].get("piotroskiScore"))
                if score is not None:
                    return {"piotroski_score": score, "source": "fmp"}
            return blank
        except Exception as exc:  # noqa: BLE001
            logger.debug("FMP piotroski score failed for %s: %s", ticker, exc)
            return blank

    def get_basic_financials(self, ticker: str) -> dict:
        """Return normalized factors: PE, PB, ROE, debt/equity.

        Cascade: Finnhub -> Alpha Vantage -> yfinance.
        """
        # 1. Cascade for baseline metrics
        baseline = None
        fh = self._from_finnhub(ticker)
        if any(fh.get(k) is not None for k in ("pe_ratio", "pb_ratio", "roe", "debt_to_equity")):
            baseline = fh
        
        if baseline is None:
            av = self._from_alphavantage(ticker)
            if any(av.get(k) is not None for k in ("pe_ratio", "pb_ratio", "roe", "debt_to_equity")):
                baseline = av
                
        if baseline is None:
            baseline = self._from_yfinance(ticker)
            
        # 2. Fetch Piotroski score from FMP independently
        fmp_data = self._from_fmp(ticker)
        
        # 3. Merge results
        baseline["piotroski_score"] = fmp_data.get("piotroski_score")
        
        # Update source string if FMP provided the score
        if fmp_data.get("piotroski_score") is not None:
            if baseline["source"] == "none":
                baseline["source"] = "fmp"
            else:
                baseline["source"] = f"{baseline['source']}+fmp"
                
        return baseline

