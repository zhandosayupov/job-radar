"""Normalized Job model. Unknown values are None, never guessed."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import text as T
from .excerpt import build_excerpt, full_text

SourceStatus = Literal["ok", "partial", "blocked", "error", "skipped"]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Job(BaseModel):
    id: str
    source: str
    company: str
    title: str
    location_text: str | None = None
    country: str | None = None
    remote_text: str | None = None
    posted_at: str | None = None
    posted_approx: bool = False
    url: str
    salary_text: str | None = None
    visa_text: str | None = None
    excerpt: str | None = None
    lang: str = "en"
    first_seen: str
    fingerprint: str
    # Plain-text description, used by prefilter/excerpt; never written to the inbox.
    description: str | None = Field(default=None, exclude=True)

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("url must be https")
        return v

    @classmethod
    def build(
        cls,
        *,
        source: str,
        company: str,
        title: str,
        url: str,
        source_job_id: str | int | None = None,
        location_text: str | None = None,
        country: str | None = None,
        remote_text: str | None = None,
        posted_at: str | None = None,
        posted_approx: bool = False,
        salary_text: str | None = None,
        description_html: str | None = None,
        description_text: str | None = None,
        first_seen: str | None = None,
    ) -> "Job":
        url_c = T.canonical_url(url)
        jid = f"{source}:{source_job_id}" if source_job_id not in (None, "") else f"{source}:{T.sha1(url_c)[:16]}"
        desc = description_text if description_text is not None else T.html_to_text(description_html)
        desc = desc or None
        title = " ".join((title or "").split())
        company = " ".join((company or "").split())
        location_text = T.clip(" ".join(location_text.split()), 200) if location_text else None
        lang_basis = f"{title} {desc or ''}"[:2000]
        return cls(
            id=jid,
            source=source,
            company=company,
            title=title,
            location_text=location_text or None,
            country=country,
            remote_text=T.clip(remote_text, 120) if remote_text else None,
            posted_at=posted_at,
            posted_approx=posted_approx,
            url=url_c,
            salary_text=T.clip(salary_text, 120) if salary_text else (T.extract_salary_text(desc or "")),
            visa_text=T.extract_visa_text(desc or "") if desc else None,
            excerpt=build_excerpt(desc),
            lang=T.detect_lang(lang_basis),
            first_seen=first_seen or utcnow_iso(),
            fingerprint=T.fingerprint(company, title, location_text),
            description=desc,
        )

    def inbox_dict(self, text_chars: int = 0) -> dict:
        """Serialized inbox item. With text_chars > 0 the cleaned full description is
        included as `text` (last key), so the labeler can decide without opening the page."""
        d = self.model_dump(mode="json")
        if text_chars > 0:
            d["text"] = full_text(self.description, text_chars)
        return d
