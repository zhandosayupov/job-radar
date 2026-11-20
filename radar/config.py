"""Config loading. Packaged defaults in radar/defaults/*.yaml; a deployment's config dir
overrides them by deep merge (dicts merge, lists and scalars replace)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILES = ("settings", "sources", "companies")


def deep_merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base[k], v) if k in base else v
        return out
    return over


def _load_default(name: str) -> dict:
    data = resources.files("radar").joinpath("defaults", f"{name}.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(data) or {}


def load_config(config_dir: Path | None) -> dict[str, dict]:
    cfg: dict[str, dict] = {}
    for name in CONFIG_FILES:
        merged = _load_default(name)
        if config_dir is not None:
            p = Path(config_dir) / f"{name}.yaml"
            if p.exists():
                merged = deep_merge(merged, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        cfg[name] = merged
    return cfg


# --- Pattern lists ------------------------------------------------------------------
# Plain entries match as whole words, case-insensitive ("c++" works too).
# "re:<regex>" is a case-insensitive regex; "RE:<regex>" is case-sensitive.


def compile_pattern(entry: str) -> re.Pattern[str]:
    if entry.startswith("RE:"):
        return re.compile(entry[3:])
    if entry.startswith("re:"):
        return re.compile(entry[3:], re.I)
    return re.compile(rf"(?<!\w){re.escape(entry)}(?!\w)", re.I)


@dataclass
class PatternList:
    entries: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._compiled = [(e, compile_pattern(e)) for e in self.entries]

    def first_match(self, text: str | None) -> str | None:
        if not text:
            return None
        for entry, pat in self._compiled:
            if pat.search(text):
                return entry
        return None

    def matches(self, text: str | None) -> bool:
        return self.first_match(text) is not None
