"""The leak guard must catch secret-shaped strings and private paths.

Fake secrets are assembled at runtime so this file itself never matches.
"""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("leak_guard", Path(__file__).parent.parent / "tools" / "leak_guard.py")
lg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lg)

BOT = "1234567890" + ":" + "AA" + "x" * 33
SESSION = "1" + "A" * 350 + "=="
GH = "gh" + "p_" + "a" * 36
EMAIL = "someone" + "@" + "gmail.com"


@pytest.mark.parametrize(
    "line,kind",
    [
        (f"TOKEN={BOT}", "telegram bot token"),
        (f"session: {SESSION}", "telethon StringSession"),
        (f"export GH={GH}", "github token"),
        ("api_hash = '" + "0123456789abcdef" * 2 + "'", "telegram api_hash"),
        ("-----BEGIN OPENSSH " + "PRIVATE KEY-----", "private key"),
        (f"contact {EMAIL}", "personal email"),
    ],
)
def test_secret_patterns(line, kind):
    problems = lg.scan_text("f.txt", line)
    assert problems and kind in problems[0]


def test_allow_marker_and_clean_text():
    assert lg.scan_text("f", f"{EMAIL}  # leak-guard: allow") == []
    assert lg.scan_text("f", "nothing to see; user_agent: job-radar/0.1 (+https://github.com/x/y)") == []
    assert lg.scan_text("f", "1234567890:not-a-token") == []


@pytest.mark.parametrize(
    "path,bad",
    [
        ("data/inbox/pending.jsonl", True),
        ("sub/data/x.json", True),
        ("me.session", True),
        (".env", True),
        (".env.local", True),
        ("config/settings.yaml", True),
        ("examples/config/settings.example.yaml", False),
        ("radar/defaults/settings.yaml", False),
        ("radar/state.py", False),
    ],
)
def test_forbidden_paths(path, bad):
    assert bool(lg.scan_path(path)) is bad


def test_scan_files_reads_content(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bad.txt").write_text(f"a\n{BOT}\n", encoding="utf-8")
    problems = lg.scan_files(["ok.py", "bad.txt"], tmp_path)
    assert problems == ["bad.txt:2: looks like a telegram bot token"]


def test_repo_itself_is_clean():
    root = Path(__file__).parent.parent
    files = [str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*")
             if p.is_file() and not any(part in {".venv", ".git", "__pycache__", ".pytest_cache"} or part.endswith(".egg-info") for part in p.relative_to(root).parts)]
    assert lg.scan_files(files, root) == []
