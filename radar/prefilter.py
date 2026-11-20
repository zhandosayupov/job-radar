"""Deterministic prefilter. Anything that passes goes to the labeler.

Rule names (counted in the manifest):
  title_excluded      title has a drop word (intern, manager, staff, ...)
  title_not_eng       title is not an engineering title
  text_excluded       clearance / US persons / must already have right to work / RU residency
  no_sponsorship      "no visa sponsorship" etc. -- only for jobs that are NOT remote-eligible
                      (a remote job open to our regions needs no visa)
  location_ru_by      onsite/remote restricted to Russia/Belarus
  location_na_au      US/Canada/Australia without positive visa text
  location_other      only in regions outside the target list
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .config import PatternList
from .models import Job

NEGATIVE_VISA = re.compile(
    r"\bno\b[^.]{0,20}\b(?:visa|sponsor)|\bnot\b[^.]{0,30}\b(?:sponsor|visa)|unable to sponsor|"
    r"without (?:visa )?sponsorship|cannot sponsor|can't sponsor|won't sponsor|"
    r"must already|existing right to work|не (?:предоставляем|оформляем|помогаем)[^.]{0,30}виз",
    re.I,
)


def visa_positive(visa_text: str | None) -> bool:
    return bool(visa_text) and not NEGATIVE_VISA.search(visa_text or "")


@dataclass
class Decision:
    keep: bool
    rule: str | None = None
    detail: str | None = None


class Prefilter:
    def __init__(self, settings: dict):
        p = settings.get("prefilter", {})
        loc = p.get("location", {})
        self.title_drop = [PatternList([e]) for e in p.get("title_drop", [])]
        self.title_drop_except = {k: PatternList(v or []) for k, v in (p.get("title_drop_except") or {}).items()}
        self.title_require = PatternList(p.get("title_require", []))
        self.text_drop = PatternList(p.get("text_drop", []))
        self.no_sponsor = PatternList(p.get("text_drop_unless_remote", []))
        self.loc_remote = PatternList(loc.get("remote", []))
        self.loc_allow = PatternList(loc.get("allow", []))
        self.loc_ru_by = PatternList(loc.get("deny_ru_by", []))
        self.loc_na_au = PatternList(loc.get("deny_unless_visa", []))
        self.loc_other = PatternList(loc.get("deny_other", []))
        self.counts: Counter[str] = Counter()

    def check(self, job: Job) -> Decision:
        d = self._check(job)
        if not d.keep and d.rule:
            self.counts[d.rule] += 1
        return d

    def _check(self, job: Job) -> Decision:
        title = job.title
        for pl in self.title_drop:
            hit = pl.first_match(title)
            if hit is None:
                continue
            exc = self.title_drop_except.get(hit)
            if exc is not None and exc.matches(title):
                continue
            return Decision(False, "title_excluded", hit)
        if not self.title_require.matches(title):
            return Decision(False, "title_not_eng", None)

        text_blob = " \n ".join(x for x in (title, job.remote_text, job.description, job.excerpt) if x)
        hit = self.text_drop.first_match(text_blob)
        if hit:
            return Decision(False, "text_excluded", hit)

        where = " | ".join(x for x in (job.location_text, job.remote_text, job.country) if x)
        allow = self.loc_allow.first_match(where)
        ru = self.loc_ru_by.first_match(where)
        na = self.loc_na_au.first_match(where)
        other = self.loc_other.first_match(where)
        remote = self.loc_remote.matches(where)
        remote_eligible = remote and (allow is not None or not (ru or na or other))

        hit = self.no_sponsor.first_match(text_blob)
        if hit and not remote_eligible:
            return Decision(False, "no_sponsorship", hit)

        if not where or allow:
            return Decision(True)
        if ru:
            return Decision(False, "location_ru_by", ru)
        if na:
            if visa_positive(job.visa_text):
                return Decision(True)
            return Decision(False, "location_na_au", na)
        if other:
            return Decision(False, "location_other", other)
        return Decision(True)  # unknown or plain "Remote": let the labeler decide
