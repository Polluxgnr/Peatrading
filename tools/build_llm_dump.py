#!/usr/bin/env python3
"""Multi-Category & Full Project LLM Context Generator for PEA Pollux.

Generates:
  1. `PROJECT_FULL_DUMP_FOR_LLM.md`: Complete monolithic dump of the entire repository.
  2. `docs/dumps/`: Specialized, modular sub-dumps per domain:
     - `DUMP_00_DATA_SENSORS.md`: Ingestion, scrapers, APIs, OpenInsider, AMF, Boursorama, ECB.
     - `DUMP_01_MEMORY_CORE.md`: SQLite schemas, DuckDB time-series, data models.
     - `DUMP_02_QUANT_ENGINE.md`: Mean-Reversion, Trend Quality, HMM regime, ML feature store, stochastic models.
     - `DUMP_03_RISK_PORTFOLIO.md`: Pydantic risk config, Drawdown breakers, HRP sizer, stress testing.
     - `DUMP_04_ORCHESTRATOR_AI.md`: Priority cascade, Red Team adversarial debate, post-mortem engine.
     - `DUMP_05_INTERFACES.md`: Streamlit Bloomberg HUD, Discord copilot, trade cards.
     - `DUMP_06_07_API_MCP.md`: Internal FastAPI SSOT & Claude Desktop MCP server.
     - `DUMP_CONFIG_AND_TESTS.md`: YAML configurations, test suites, deployment specs.

Usage:
  python tools/build_llm_dump.py
  # or: make dump
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_llm_dump")

ROOT = Path(__file__).resolve().parent.parent
DUMPS_DIR = ROOT / "docs" / "dumps"
GLOBAL_OUT = ROOT / "PROJECT_FULL_DUMP_FOR_LLM.md"

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
    "docs/dumps",
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
    "Makefile",
    "api_keys.env.example",
    ".gitignore",
}

SKIP_FILES = {
    "PROJECT_FULL_DUMP_FOR_LLM.md",
}

# Mapping of specialized categories
CATEGORIES: Dict[str, Dict[str, Any]] = {
    "DUMP_00_DATA_SENSORS.md": {
        "title": "Data Sensors, Scrapers & External Ingestion Layer",
        "match": lambda p: p.parts[0] == "00_data_sensors" or "newsletter_ingest" in str(p),
    },
    "DUMP_01_MEMORY_CORE.md": {
        "title": "Memory Core, State Persistence & Data Contracts",
        "match": lambda p: p.parts[0] == "01_memory_core",
    },
    "DUMP_02_QUANT_ENGINE.md": {
        "title": "Quantitative Strategy, Indicators, HMM Regimes & ML Feature Store",
        "match": lambda p: p.parts[0] == "02_quant_engine",
    },
    "DUMP_03_RISK_PORTFOLIO.md": {
        "title": "Risk Sentinel, Pydantic Config, Drawdown Breakers & HRP Sizer",
        "match": lambda p: p.parts[0] == "03_risk_portfolio",
    },
    "DUMP_04_ORCHESTRATOR_AI.md": {
        "title": "AI Orchestration, Priority Cascade, Red Team Debate & Post-Mortem",
        "match": lambda p: p.parts[0] == "04_orchestrator_ai",
    },
    "DUMP_05_INTERFACES.md": {
        "title": "Interfaces, Streamlit Bloomberg Terminal HUD & Discord Copilot",
        "match": lambda p: p.parts[0] == "05_interfaces" or p.name == "run_dashboard.ps1" or p.name == "run_discord.py",
    },
    "DUMP_06_07_API_MCP.md": {
        "title": "Internal FastAPI Gateway & Claude Desktop MCP Server",
        "match": lambda p: p.parts[0] in ("06_api", "07_mcp"),
    },
    "DUMP_CONFIG_AND_TESTS.md": {
        "title": "Configuration Yaml, Test Suites, Root Ops & Documentation",
        "match": lambda p: p.parts[0] in ("config", "tests", "tools", ".github") or p.name in ("Makefile", "requirements.txt", "Dockerfile", "docker-compose.yml", "seed_account.py", "main_scheduler.py", "README.md"),
    },
}


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
    if "dumps" in path.parts:
        return False
    if path.name in NAME_ALLOW:
        return True
    if path.suffix.lower() in EXTS:
        if path.suffix.lower() == ".env" or path.name.endswith(".env"):
            return path.name.endswith(".env.example")
        return True
    return False


def collect_files() -> List[Path]:
    files: List[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if _should_include(rel):
            files.append(rel)
    return files


def generate_dump_content(title: str, file_subset: List[Path]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = [
        f"# PEA Pollux — {title}",
        f"Generated: `{stamp}` | File Count: `{len(file_subset)}`",
        "Institutional Systematic Decision Support Architecture for French PEA.",
        "---",
        "## Included Files Index",
    ]
    for rel in file_subset:
        lines.append(f"- [{rel.as_posix()}](#file-{rel.as_posix().replace('/', '-').replace('.', '-')})")
    lines.append("")
    lines.append("---")

    for rel in file_subset:
        abs_path = ROOT / rel
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        safe = text.replace("```", "``\u200b`")
        lines.append(f"## FILE: {rel.as_posix()}")
        lines.append(f"```{_lang(rel)}")
        lines.append(safe.rstrip() + "\n```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    all_files = collect_files()
    logger.info("Collected %d candidate source files across repository.", len(all_files))

    # 1. Global Monolithic Dump
    global_content = generate_dump_content("Complete Monolithic Repository Dump", all_files)
    GLOBAL_OUT.write_text(global_content, encoding="utf-8")
    logger.info("Wrote global dump %s (%d files, %.1f KB)", GLOBAL_OUT.name, len(all_files), GLOBAL_OUT.stat().st_size / 1024)

    # 2. Specialized Modular Dumps
    for dump_filename, meta in CATEGORIES.items():
        matched = [f for f in all_files if meta["match"](f)]
        out_path = DUMPS_DIR / dump_filename
        cat_content = generate_dump_content(meta["title"], matched)
        out_path.write_text(cat_content, encoding="utf-8")
        logger.info("Wrote category dump %s (%d files, %.1f KB)", dump_filename, len(matched), out_path.stat().st_size / 1024)

    print("\n[OK] All project LLM dumps generated successfully in `docs/dumps/` and root!")


if __name__ == "__main__":
    main()
