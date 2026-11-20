"""Fail if anything private is about to land in this public repo.

    python tools/leak_guard.py            # scan all tracked + staged files
    python tools/leak_guard.py --staged   # scan staged files only (pre-commit hook)

Checks forbidden paths (data/, sessions, .env, deployment configs) and secret-shaped
content (Telegram bot tokens, Telethon StringSessions, GitHub tokens, api_hash, private
keys, personal email addresses). A line can opt out with the marker `leak-guard: allow`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PATHS = [
    re.compile(r"(^|/)data/"),
    re.compile(r"\.session(-journal)?$"),
    re.compile(r"(^|/)\.env(\.|$)"),
    re.compile(r"^config/(settings|sources|companies)\.yaml$"),
    re.compile(r"(^|/)dryrun_out/"),
]

SECRET_PATTERNS = {
    "telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b"),
    "telethon StringSession": re.compile(r"\b1[A-Za-z0-9_-]{340,}={0,2}"),
    "github token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    "telegram api_hash": re.compile(r"api_hash\s*[:=]\s*['\"]?[0-9a-f]{32}\b", re.I),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "personal email": re.compile(r"\b[A-Za-z0-9._%+-]+@(?:gmail|yandex|mail|outlook|hotmail|icloud)\.(?:com|ru|kz)\b", re.I),
}
ALLOW_MARKER = "leak-guard: allow"
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz"}


def scan_text(path: str, text: str) -> list[str]:
    problems = []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(line):
                problems.append(f"{path}:{i}: looks like a {name}")
    return problems


def scan_path(path: str) -> list[str]:
    norm = path.replace("\\", "/")
    for pat in FORBIDDEN_PATHS:
        if pat.search(norm):
            return [f"{norm}: path must never be committed to the public repo"]
    return []


def scan_files(paths: list[str], root: Path) -> list[str]:
    problems: list[str] = []
    for p in paths:
        problems += scan_path(p)
        f = root / p
        if not f.is_file() or f.suffix.lower() in BINARY_EXT:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems += scan_text(p, text)
    return problems


def git_files(staged_only: bool) -> list[str]:
    if staged_only:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str]) -> int:
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip())
    problems = scan_files(git_files("--staged" in argv), root)
    if problems:
        print("leak-guard: FAILED")
        for p in problems:
            print("  " + p)
        return 1
    print("leak-guard: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
