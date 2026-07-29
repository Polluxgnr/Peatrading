"""Drawdown circuit breaker — enforces DAILY/WEEKLY/MONTHLY_MAX_LOSS_PCT.

Reads portfolio_history from SQLite to calculate rolling PnL and vetoes
all new BUY signals when any threshold is breached.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from sqlite_portfolio import PortfolioDB  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = _ROOT / "config"


class DrawdownBreaker:
    """Hard veto when rolling PnL breaches configured loss limits."""

    def __init__(self, config_dir: Path | str | None = None) -> None:
        cfg_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG
        risk_file = cfg_path / "risk_params.yaml"
        risk: dict = {}
        if risk_file.exists():
            with open(risk_file, "r", encoding="utf-8") as fh:
                risk = yaml.safe_load(fh) or {}

        self.daily_limit = float(risk.get("DAILY_MAX_LOSS_PCT", -0.005))
        self.weekly_limit = float(risk.get("WEEKLY_MAX_LOSS_PCT", -0.02))
        self.monthly_limit = float(risk.get("MONTHLY_MAX_LOSS_PCT", -0.05))

    def check(self, portfolio_db: PortfolioDB | None = None) -> tuple[bool, str]:
        """Return (is_breached, reason). True means VETO all new buys."""
        if portfolio_db is None:
            return False, ""

        try:
            history = portfolio_db.get_portfolio_history(days=31)
        except Exception:  # noqa: BLE001
            return False, ""

        if not history or len(history) < 2:
            return False, ""

        # history is list of dicts with 'date' and 'total_value' keys
        sorted_hist = sorted(history, key=lambda r: r.get("date", ""))
        if len(sorted_hist) < 2:
            return False, ""

        latest_val = float(sorted_hist[-1].get("total_value", 0))
        if latest_val <= 0:
            return False, ""

        now = datetime.now(timezone.utc).date()

        def _pnl_since(days_back: int) -> float | None:
            cutoff = now - timedelta(days=days_back)
            candidates = [
                r for r in sorted_hist
                if str(r.get("date", ""))[:10] <= str(cutoff)
            ]
            if not candidates:
                return None
            ref_val = float(candidates[-1].get("total_value", 0))
            if ref_val <= 0:
                return None
            return (latest_val - ref_val) / ref_val

        daily_pnl = _pnl_since(1)
        weekly_pnl = _pnl_since(7)
        monthly_pnl = _pnl_since(30)

        if daily_pnl is not None and daily_pnl < self.daily_limit:
            return True, f"DRAWDOWN VETO: daily PnL {daily_pnl:.2%} < {self.daily_limit:.2%}"
        if weekly_pnl is not None and weekly_pnl < self.weekly_limit:
            return True, f"DRAWDOWN VETO: weekly PnL {weekly_pnl:.2%} < {self.weekly_limit:.2%}"
        if monthly_pnl is not None and monthly_pnl < self.monthly_limit:
            return True, f"DRAWDOWN VETO: monthly PnL {monthly_pnl:.2%} < {self.monthly_limit:.2%}"

        return False, ""
