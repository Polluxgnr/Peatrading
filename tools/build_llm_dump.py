#!/usr/bin/env python3
"""Regenerate PROJECT_FULL_DUMP_FOR_LLM.md for one-shot LLM context.

Usage (from repo root):
    python tools/build_llm_dump.py
    python tools/build_llm_dump.py --no-summary   # skip architecture preamble
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "PROJECT_FULL_DUMP_FOR_LLM.md"
README = ROOT / "README.md"

SKIP_DIRS = {
    ".git",
    "venv_x64",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".cursor",
    "database",
    "mcps",
    "agent-transcripts",
    "terminals",
}

EXTS = {
    ".py",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".ps1",
    ".json",
    ".ini",
    ".cfg",
}

NAME_ALLOW = {
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "api_keys.env.example",
    ".gitignore",
}

SKIP_FILES = {
    "PROJECT_FULL_DUMP_FOR_LLM.md",
}

# High-signal files surfaced first in the index (read these before the rest).
PRIORITY_FILES = [
    "README.md",
    "config/risk_params.yaml",
    "config/pea_universe.yaml",
    "01_memory_core/data_models.py",
    "01_memory_core/sqlite_portfolio.py",
    "01_memory_core/duckdb_manager.py",
    "02_quant_engine/technical_scorer.py",
    "02_quant_engine/quantitative_math.py",
    "02_quant_engine/stochastic_models.py",
    "03_risk_portfolio/stress_tester.py",
    "03_risk_portfolio/pea_position_sizer.py",
    "04_orchestrator_ai/signal_priority_cascade.py",
    "04_orchestrator_ai/red_team_agent.py",
    "05_interfaces/terminal_dashboard.py",
    "main_scheduler.py",
]

ARCHITECTURE_SUMMARY = """\
## Architecture snapshot (for agents)

| Layer | Path | Role |
|-------|------|------|
| Sensors | `00_data_sensors/` | OHLCV, VIX, insiders (AMF→FMP→YF), Polymarket, Bourso scrapers, newsletter IMAP |
| Memory | `01_memory_core/` | Pydantic models, SQLite (`portfolio`, `audit_logs`, `portfolio_history`, **`news_history`**), DuckDB OHLCV |
| Quant | `02_quant_engine/` | Ensemble conviction scorer (MR + vol + insider + inst + **news/poly modifiers**), Smart DCA |
| Risk | `03_risk_portfolio/` | Cascade vetoes, Half-Kelly sizing, correlation firewall, ATR rebalancer |
| Orchestrator | `04_orchestrator_ai/` | Pipeline conductor, earnings blackout, macro veto, revocation, weekly historian |
| UI | `05_interfaces/` | Streamlit Mission Control — **native HTML ticker tape**, exploration 600+ tickers, live telemetry tab |
| Ops | `main_scheduler.py` | Paris daemon (09:00 / 13:30 / 17:10 + briefing 08:25 + ATR 08:35) |

**Dashboard highlights (Phase 26–28):**
- Auto-sync on session open (`load_universe`, `get_last_prices`, `get_vix`)
- Native CSS marquee tape (no TradingView widget for `.PA`)
- `news_history` SQLite archive — exact timestamps, cross-session memory
- Portfolio tab: explicit ATR 2.5× stop table
- Exploration: universal ticker search, order ticket, decision checklist
- Architecture tab: live source health + active `risk_params.yaml` + logic expanders

**Hard rules:** no auto-broker execution · LLM explains only · conviction emit ≥ 65 · manual Discord/Streamlit approve.
"""


def _read_phase_from_readme() -> str:
    try:
        first = README.read_text(encoding="utf-8").splitlines()[0]
        m = re.search(r"Phase\s+[\d–\-]+", first)
        return m.group(0) if m else "PEA Pollux"
    except OSError:
        return "PEA Pollux"


def _lang(path: Path) -> str:
    return {
        ".py": "python",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".txt": "text",
        ".ps1": "powershell",
        ".json": "json",
        ".ini": "ini",
        ".cfg": "ini",
    }.get(path.suffix.lower(), "text")


def _should_include(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name in NAME_ALLOW:
        return True
    if path.suffix.lower() in EXTS:
        if path.suffix.lower() == ".env" or path.name.endswith(".env"):
            return path.name.endswith(".env.example")
        return True
    return False


def collect_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if _should_include(rel):
            files.append(rel)
    return files


def _group_index(files: list[Path]) -> list[str]:
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for rel in files:
        parent = rel.parent.as_posix() if rel.parent != Path(".") else "(root)"
        by_dir[parent].append(rel)

    lines: list[str] = []
    for parent in sorted(by_dir.keys(), key=lambda x: (x != "(root)", x)):
        lines.append(f"### `{parent}/`")
        for rel in sorted(by_dir[parent], key=lambda p: p.name.lower()):
            try:
                nlines = len((ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                nlines = 0
            prio = " ⭐" if rel.as_posix() in PRIORITY_FILES else ""
            lines.append(f"- `{rel.as_posix()}` ({nlines} lines){prio}")
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Build PROJECT_FULL_DUMP_FOR_LLM.md")
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Omit architecture snapshot preamble",
    )
    args = parser.parse_args()

    files = collect_files()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    phase = _read_phase_from_readme()

    lines: list[str] = [
        "# PEA Pollux — Full Project Dump for LLM",
        "",
        f"> **{phase}** · Generated `{stamp}` · Root `{ROOT}`",
        "",
        "One-shot context for external LLM agents. Includes source, configs, and docs.",
        "Excludes: `venv*`, `database/*.db`, secrets, nested dump, agent transcripts.",
        "",
        "---",
    ]

    if not args.no_summary:
        lines.append(ARCHITECTURE_SUMMARY)
        lines.append("---")
        lines.append("")
        lines.append("### Priority files (read first)")
        for p in PRIORITY_FILES:
            if (ROOT / p).exists():
                lines.append(f"- `{p}`")
        lines.append("")
        lines.append("---")

    lines.append(f"## File index ({len(files)} files)")
    lines.extend(_group_index(files))
    lines.append("---")

    for rel in files:
        abs_path = ROOT / rel
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        safe = text.replace("```", "``\u200b`")
        nlines = len(text.splitlines())
        lines.append(f"## FILE: {rel.as_posix()} ({nlines} lines)")
        lines.append(f"```{_lang(rel)}")
        lines.append(safe.rstrip() + "\n```")
        lines.append("")

    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.name}: {len(files)} files, {size_kb:.0f} KB ({phase})")


if __name__ == "__main__":
    main()
