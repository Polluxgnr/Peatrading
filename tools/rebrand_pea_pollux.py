#!/usr/bin/env python3
"""One-shot UTF-8 safe rebrand: PEA Pollux -> PEA Pollux."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git",
    "venv_x64",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "database",
    "logs",
    "node_modules",
}

EXTS = {".py", ".yaml", ".yml", ".md", ".ps1", ".txt", ".json", ".ini", ".cfg"}
NAMES = {"Dockerfile", "docker-compose.yml", "requirements.txt", ".gitignore"}

REPLACEMENTS = [
    ("PEA Pollux", "PEA Pollux"),
    ("PEA Pollux", "PEA Pollux"),
    ("pea_pollux", "pea_pollux"),
    ("pea_pollux_all.log", "pea_pollux_all.log"),
    ("PEA-Pollux", "PEA-Pollux"),
    ("PEA Pollux | Terminal", "PEA Pollux | Terminal"),
    ("PEA Pollux", "PEA Pollux"),
    ("PEA Pollux", "PEA Pollux"),
    ("Pollux Gronier — PEA Pollux", "Pollux Gronier — PEA Pollux"),
    ("Pollux Gronier — PEA Pollux", "Pollux Gronier — PEA Pollux"),
]


def should_touch(path: Path) -> bool:
    if path.name == "PROJECT_FULL_DUMP_FOR_LLM.md":
        return False
    if any(p in SKIP_DIRS for p in path.parts):
        return False
    if path.name in NAMES:
        return True
    return path.suffix.lower() in EXTS


def main() -> None:
    changed = 0
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or not should_touch(path):
            continue
        text = path.read_text(encoding="utf-8")
        orig = text
        for old, new in REPLACEMENTS:
            text = text.replace(old, new)
        if text != orig:
            path.write_text(text, encoding="utf-8", newline="\n")
            changed += 1
    print(f"Rebranded {changed} files.")


if __name__ == "__main__":
    main()
