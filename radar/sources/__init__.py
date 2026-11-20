"""Source registry: name in sources.yaml -> class."""
from __future__ import annotations

from .base import Source
from .greenhouse import GreenhouseSource
from .hn import HNSource
from .wwr import WWRSource

REGISTRY: dict[str, type[Source]] = {
    "greenhouse": GreenhouseSource,
    "hn": HNSource,
    "wwr": WWRSource,
}


def build_sources(sources_cfg: dict) -> list[Source]:
    out: list[Source] = []
    for name, cfg in (sources_cfg.get("sources") or {}).items():
        cfg = cfg or {}
        if not cfg.get("enabled", True):
            continue
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"unknown source in sources.yaml: {name}")
        out.append(cls(cfg))
    return out
