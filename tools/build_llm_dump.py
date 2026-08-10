#!/usr/bin/env python3
"""Regenerate PROJECT_FULL_DUMP_FOR_LLM.md for one-shot LLM context.

Usage (from repo root):
    python tools/build_llm_dump.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "PROJECT_FULL_DUMP_FOR_LLM.md"

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

# Never embed the dump inside itself, or huge generated noise.
SKIP_FILES = {
    "PROJECT_FULL_DUMP_FOR_LLM.md",
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
    if path.name in NAME_ALLOW:
        return True
    if path.suffix.lower() in EXTS:
        # Prefer the example secrets file only (never real .env).
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


def main() -> None:
    files = collect_files()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# PEA Sniper Terminal — Full Project Dump for LLM",
        f"Root: `{ROOT}`",
        f"Generated: {stamp}",
        "One-shot context dump of source, configs, and docs (no venv, no DBs, no secrets).",
        "---",
        f"## File index ({len(files)} files)",
    ]
    for rel in files:
        lines.append(f"- {rel.as_posix()}")
    lines.append("")
    lines.append("---")

    for rel in files:
        abs_path = ROOT / rel
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        # Fence safety: close any accidental triple-backticks in source.
        safe = text.replace("```", "``\u200b`")
        lines.append(f"## FILE: {rel.as_posix()}")
        lines.append(f"```{_lang(rel)}")
        lines.append(safe.rstrip() + "\n```")
        lines.append("")

    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.name}: {len(files)} files, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
