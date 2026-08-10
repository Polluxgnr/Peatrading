"""Tests for trade-card helpers and newsletter dedupe (no network)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("01_memory_core", "03_risk_portfolio", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))
sys.path.insert(0, str(ROOT / "experiments" / "newsletter_ingest"))

from data_models import Position, PortfolioState, Signal, SignalType  # noqa: E402
from pea_position_sizer import PeaSizer  # noqa: E402
from trade_cards import conviction_tier, atr_risk_line, sector_impact_line  # noqa: E402
from ingest.dedupe import dedupe_articles  # noqa: E402


def test_sizing_explanation_keys():
    sizer = PeaSizer(ROOT / "config")
    pf = PortfolioState(
        cash_available=8000,
        total_equity=20000,
        positions=[],
        last_updated=datetime.now(timezone.utc),
    )
    sig = Signal(ticker="AI.PA", signal_type=SignalType.BUY, score=90.0)
    qty, meta = sizer.size_with_explanation(sig, pf, 180.0, historical_volatility=0.25)
    assert qty >= 0
    assert "kelly_fraction" in meta and "weight_pct" in meta
    assert meta["vol_factor"] > 0


def test_conviction_and_atr_risk_copy():
    assert conviction_tier(92)[0] == "Tier A"
    assert conviction_tier(80)[0] == "Tier B"
    line = atr_risk_line(10, 2.0, 2.5, 10000)
    assert "−" in line or "-" in line
    assert "equity" in line.lower() or "Equity" in line or "%" in line


def test_sector_impact_sentence():
    pf = PortfolioState(
        cash_available=1000,
        total_equity=10000,
        positions=[
            Position(
                ticker="MC.PA", qty_shares=1, avg_entry_price=600,
                current_price=600, sector="Luxury",
            )
        ],
        last_updated=datetime.now(timezone.utc),
    )
    line = sector_impact_line(pf, "KER.PA", "Luxury", 500, 10000, 25)
    assert "Luxury" in line and "→" in line


def test_newsletter_dedupe_collapses_near_dupes():
    arts = [
        {"title": "LVMH beats estimates on strong US demand", "url": "https://a/1"},
        {"title": "LVMH beats estimates on strong U.S. demand!", "url": "https://b/2"},
        {"title": "Air Liquide wins big industrial contract", "url": "https://c/3"},
    ]
    out = dedupe_articles(arts)
    assert len(out) == 2
