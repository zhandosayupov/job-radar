"""Generic Greenhouse adapter. Boards come from companies.yaml (ats: greenhouse).

One request per board: /v1/boards/<token>/jobs?content=true (descriptions included, so no
per-job detail calls). Optional `departments: [ids]` filter is applied client-side.
"""
from __future__ import annotations

from ..http import Http
from ..models import Job
from .base import FetchOutcome, RunContext, Source, iso_utc

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse_board(payload: dict, company_cfg: dict, now_iso: str) -> list[Job]:
    dept_filter = {int(d) for d in company_cfg.get("departments") or []}
    jobs: list[Job] = []
    for j in payload.get("jobs", []):
        if dept_filter and not any(int(d.get("id", -1)) in dept_filter for d in j.get("departments") or []):
            continue
        first_pub = iso_utc(j.get("first_published"))
        posted, approx = (first_pub, False) if first_pub else (iso_utc(j.get("updated_at")), True)
        jobs.append(
            Job.build(
                source="greenhouse",
                source_job_id=j["id"],
                company=company_cfg.get("name") or j.get("company_name") or company_cfg["token"],
                title=j.get("title", ""),
                url=j.get("absolute_url") or f"https://boards.greenhouse.io/{company_cfg['token']}/jobs/{j['id']}",
                location_text=(j.get("location") or {}).get("name"),
                posted_at=posted,
                posted_approx=approx,
                description_html=j.get("content"),
                first_seen=now_iso,
            )
        )
    return jobs


class GreenhouseSource(Source):
    name = "greenhouse"
    kind = "ats"

    def fetch(self, http: Http, ctx: RunContext) -> FetchOutcome:
        out = FetchOutcome()
        boards = [c for c in ctx.companies if c.get("ats") == "greenhouse" and c.get("enabled", True)]
        for comp in boards:
            token = comp["token"]

            def one(comp=comp, token=token) -> None:
                payload = http.get_json(API.format(token=token), params={"content": "true"})
                jobs = parse_board(payload, comp, ctx.now_iso)
                out.jobs.extend(jobs)
                if comp.get("complete"):
                    out.complete_sets[f"greenhouse:{token}"] = {j.id for j in jobs}

            out.run_unit(token, one)
        return out
