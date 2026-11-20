"""Source interface. One module per source; each returns normalized Jobs.

A source may be made of several units (e.g. one Greenhouse board per company). A failing
unit makes the source `partial`; all units failing makes it `blocked`/`error`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..http import Blocked, FetchError, Http
from ..models import Job


@dataclass
class RunContext:
    now_iso: str
    settings: dict
    companies: list[dict]
    cursors: dict = field(default_factory=dict)  # per-source persistent cursors (e.g. last post id)


@dataclass
class FetchOutcome:
    jobs: list[Job] = field(default_factory=list)
    units_ok: int = 0
    blocked: list[str] = field(default_factory=list)  # unit: reason
    errors: list[str] = field(default_factory=list)
    # unit key -> full list of ids currently on that board (only for complete units)
    complete_sets: dict[str, set[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        bad = len(self.blocked) + len(self.errors)
        if bad == 0:
            return "ok"
        if self.units_ok > 0:
            return "partial"
        return "blocked" if self.blocked and not self.errors else "error"

    def run_unit(self, unit: str, fn) -> None:
        """Run fn() for one unit, recording Blocked/FetchError instead of raising."""
        try:
            fn()
            self.units_ok += 1
        except Blocked as e:
            self.blocked.append(f"{unit}: {e}")
        except FetchError as e:
            self.errors.append(f"{unit}: {e}")
        except Exception as e:  # parser bugs must not kill the run
            self.errors.append(f"{unit}: {type(e).__name__}: {str(e)[:200]}")


class Source(ABC):
    name: str = ""
    tier: int = 1
    kind: str = "aggregator"  # ats | aggregator | community (dedupe preference: ats wins)
    complete: bool = False

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.tier = int(self.cfg.get("tier", type(self).tier))

    @abstractmethod
    def fetch(self, http: Http, ctx: RunContext) -> FetchOutcome: ...


# --- date helpers ------------------------------------------------------------------


def iso_utc(value: str | int | float | None) -> str | None:
    """Normalize ISO-8601 strings, RFC-822 dates or unix seconds to ISO UTC. None if unparsable."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            s = str(value).strip()
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
