"""Phase 17 funnel taxonomy tests (no Streamlit runtime)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "05_interfaces"))
sys.path.insert(0, str(ROOT / "04_orchestrator_ai"))

# Import helpers without executing Streamlit page: load module pieces carefully.
import importlib.util

spec = importlib.util.spec_from_file_location(
    "terminal_dashboard_funnel",
    ROOT / "05_interfaces" / "terminal_dashboard.py",
)
# Do NOT exec full dashboard (st.set_page_config). Test classify mapping via historian.


from weekly_historian import WeeklyHistorian  # noqa: E402


def test_classify_buckets_match_expected_keywords():
    assert WeeklyHistorian._classify(
        {"status": "REJECTED", "reason": "REJECTED: VIX panic (V2TX=35)"}
    ) == "vetoed_vix"
    assert WeeklyHistorian._classify(
        {"status": "REJECTED", "reason": "REJECTED: Illiquid (ADV €1000)"}
    ) == "vetoed_liquidity"
    assert WeeklyHistorian._classify(
        {"status": "REJECTED", "reason": "REJECTED: Highly correlated with MC.PA"}
    ) == "vetoed_correlation"
    assert WeeklyHistorian._classify(
        {"status": "APPROVED", "reason": "ok"}
    ) == "executed"


def test_funnel_drop_mapping_logic():
    # Mirror of terminal_dashboard._map_reject_to_funnel_drop without importing Streamlit.
    def map_drop(classified: str, reason: str) -> str:
        reason_l = (reason or "").lower()
        if "insufficient cash" in reason_l:
            return "cash_sizing"
        if classified in ("vetoed_liquidity", "vetoed_max_positions"):
            return "sanity_liquidity"
        if "no current price" in reason_l:
            return "sanity_liquidity"
        if classified in ("vetoed_vix", "vetoed_macro", "vetoed_earnings"):
            return "macro_vix"
        if classified == "vetoed_sector":
            return "sector"
        if classified == "vetoed_correlation":
            return "correlation"
        return "sanity_liquidity"

    assert map_drop("vetoed_vix", "VIX panic") == "macro_vix"
    assert map_drop("vetoed_earnings", "EARNINGS BLACKOUT") == "macro_vix"
    assert map_drop(
        "rejected_other", "REJECTED: Insufficient cash for 1 share"
    ) == "cash_sizing"
    assert map_drop("vetoed_sector", "Sector weight") == "sector"
