"""Entry point.

    python -m radar.run --config ../job-radar-ops/config --data ../job-radar-ops/data
    python -m radar.run --config ... --data ... --dry-run      # nothing in data/ changes

Pipeline: budget guard -> labels ingestion -> fetch (per source, isolated) -> seen check
-> prefilter -> cross-source dedupe -> closures -> inbox cap -> manifest/health/alerts
-> usage accounting.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import budget, health
from .alerts import Alerter, format_events
from .config import load_config
from .dedupe import KIND_RANK, merge
from .http import Http
from .inbox import expire, split_inbox
from .models import Job
from .prefilter import Prefilter
from .sources import REGISTRY, build_sources
from .sources.base import FetchOutcome, RunContext
from .state import DataStore, write_jsonl


@dataclass
class RunReport:
    mode: str
    new_total: int = 0
    pending_count: int = 0
    overflow: int = 0
    manifest: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    data_dir: Path | None = None


def _rank(source: str) -> int:
    cls = REGISTRY.get(source.split(":")[0])
    return KIND_RANK.get(cls.kind if cls else "aggregator", 1)


def _run_url() -> str | None:
    if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_REPOSITORY"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        return f"{server}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return None


def _dropped_row(job: Job, rule: str, detail: str | None) -> dict:
    return {
        "id": job.id,
        "company": job.company,
        "title": job.title,
        "where": " | ".join(x for x in (job.location_text, job.remote_text) if x)[:160] or None,
        "rule": rule,
        "hit": detail,
        "url": job.url,
    }


def run(
    config_dir: Path | None,
    data_dir: Path,
    *,
    dry_run: bool = False,
    only_sources: set[str] | None = None,
    http: Http | None = None,
    now: datetime | None = None,
    alerter: Alerter | None = None,
    log=print,
) -> RunReport:
    now = now or datetime.now(timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat()
    t_start = time.monotonic()
    cfg = load_config(config_dir)
    settings = cfg["settings"]
    store = DataStore(data_dir)
    started = budget.job_started(now)

    # --- budget guard ------------------------------------------------------
    usage = budget.load_usage(store, now)
    mode = budget.decide_mode(int(usage.get("total_minutes", 0)), settings.get("budget", {}))
    sources = build_sources(cfg["sources"])
    if only_sources:
        sources = [s for s in sources if s.name in only_sources]
    report = RunReport(mode=mode, data_dir=data_dir)
    per_source: dict[str, dict] = {}

    if mode == "stop":
        log(f"budget: month total {usage.get('total_minutes')} min > hard limit; exiting early")
        for s in sources:
            per_source[s.name] = {"status": "skipped", "fetched": 0, "new": 0, "dropped_by_rule": {}, "detail": "budget hard limit"}
        manifest = {
            "generated_at": now_iso,
            "mode": mode,
            "notice": "budget hard limit reached; no sources fetched",
            "pending_count": len(store.load_pending()),
            "sources": per_source,
            "month_minutes_used": usage.get("total_minutes", 0),
        }
        if not dry_run:
            store.save("inbox/manifest.json", manifest)
            budget.record_run(store, started, datetime.now(timezone.utc), mode)
        report.manifest = manifest
        return report

    # --- state ------------------------------------------------------------------
    inbox_cfg = settings.get("inbox", {})
    seen = store.load_seen(now.date(), int(settings.get("seen_months", 6)))
    labeled = store.labeled_ids()
    pending_all = store.load_pending()
    pending = [p for p in pending_all if p["id"] not in labeled]
    removed_labeled = len(pending_all) - len(pending)
    pending, expired = expire(pending, now, int(inbox_cfg.get("pending_max_age_days", 45)))
    boards: dict = store.load("boards.json", {})
    cursors: dict = store.load("cursors.json", {})

    # --- fetch --------------------------------------------------------------------
    hcfg = settings.get("http", {})
    own_http = http is None
    if http is None:
        http = Http(
            user_agent=hcfg.get("user_agent", "job-radar/0.1"),
            min_interval_s=float(hcfg.get("min_interval_s", 1.0)),
            timeout_s=float(hcfg.get("timeout_s", 20)),
            retries=int(hcfg.get("retries", 2)),
        )
    ctx = RunContext(now_iso=now_iso, settings=settings, companies=cfg["companies"].get("companies", []), cursors=cursors)
    prefilter = Prefilter(settings)
    run_budget_s = float(settings.get("budget", {}).get("run_time_budget_s", 480))
    new_items: list[dict] = []
    new_ids: set[str] = set()
    dropped_rows: list[dict] = []
    closed_ids: set[str] = set()

    try:
        for src in sorted(sources, key=lambda s: s.tier):
            if mode == "tier1" and src.tier > 1:
                per_source[src.name] = {"status": "skipped", "fetched": 0, "new": 0, "dropped_by_rule": {}, "detail": "budget soft limit: tier-1 only"}
                continue
            if time.monotonic() - t_start > run_budget_s:
                per_source[src.name] = {"status": "skipped", "fetched": 0, "new": 0, "dropped_by_rule": {}, "detail": "run time budget exhausted"}
                continue
            t0 = time.monotonic()
            try:
                outcome = src.fetch(http, ctx)
            except Exception as e:  # a source must never kill the run
                outcome = FetchOutcome(errors=[f"{type(e).__name__}: {str(e)[:200]}"])
            drops: Counter[str] = Counter()
            n_new = 0
            for job in outcome.jobs:
                if job.id in seen or job.id in new_ids:
                    continue
                new_ids.add(job.id)
                n_new += 1
                d = prefilter.check(job)
                if d.keep:
                    new_items.append(job.inbox_dict())
                else:
                    drops[d.rule or "?"] += 1
                    dropped_rows.append(_dropped_row(job, d.rule or "?", d.detail))
            for key, ids in outcome.complete_sets.items():
                prev = boards.get(key)
                if not ids and prev:
                    outcome.notes.append(f"{key}: empty board, closures skipped")
                    continue
                if prev is not None:
                    closed_ids |= set(prev) - ids
                boards[key] = sorted(ids)
            detail = "; ".join(outcome.blocked + outcome.errors + outcome.notes)
            per_source[src.name] = {
                "status": outcome.status,
                "fetched": len(outcome.jobs),
                "new": n_new,
                "kept": n_new - sum(drops.values()),
                "dropped_by_rule": dict(sorted(drops.items())),
                "seconds": round(time.monotonic() - t0, 1),
                "detail": detail[:500] or None,
            }
            log(f"  {src.name:<12} {outcome.status:<8} fetched={len(outcome.jobs):<4} new={n_new:<4} kept={n_new - sum(drops.values()):<4} {detail[:120]}")
    finally:
        if own_http:
            http.close()

    # --- merge / inbox ---------------------------------------------------------------
    if closed_ids:
        pending = [p for p in pending if p["id"] not in closed_ids]
    merged, dupes = merge(pending, new_items, _rank)
    inbox, overflow, tokens = split_inbox(
        merged, int(inbox_cfg.get("max_items", 150)), int(inbox_cfg.get("max_tokens", 60000))
    )

    # --- health / alerts ---------------------------------------------------------------
    health_state: dict = store.load("health.json", {})
    events = health.update(health_state, per_source, now_iso)
    issue_after = int(settings.get("health", {}).get("issue_after_failures", 3))
    persistent = sorted(n for n in per_source if health.consecutive_bad(health_state, n) >= issue_after)

    manifest = {
        "generated_at": now_iso,
        "mode": mode,
        "pending_count": len(inbox),
        "overflow": len(overflow),
        "inbox_tokens_est": tokens,
        "new_total": len(new_ids),
        "kept_total": len(new_items),
        "cross_source_duplicates": dupes,
        "removed_labeled": removed_labeled,
        "removed_closed": len(closed_ids),
        "expired": expired,
        "dropped_by_rule": dict(sorted(prefilter.counts.items())),
        "sources": per_source,
        "persistent_failures": persistent,
        "month_minutes_used": int(usage.get("total_minutes", 0)),
    }
    report.new_total = len(new_ids)
    report.pending_count = len(inbox)
    report.overflow = len(overflow)
    report.events = events

    # --- persist (dry-run callers pass a throwaway copy of data/) ----------------------
    store.append_seen(new_ids, now.date())
    store.save_pending(inbox, overflow)
    write_jsonl(store.root / "inbox" / "dropped_last.jsonl", dropped_rows)
    store.save("boards.json", boards)
    store.save("cursors.json", cursors)
    store.save("health.json", health_state)
    if closed_ids:
        store.append_closed(closed_ids, now)

    text = format_events(events, _run_url())
    if text and not dry_run:
        alerter = alerter or Alerter()
        sent = alerter.send(text)
        why = "sent" if sent else f"not sent ({alerter.last_error or 'not configured'})"
        log(f"alerts: {len(events)} event(s), {why}")
    elif text:
        log("alerts (dry-run, not sent):\n" + text)

    if not dry_run:
        usage = budget.record_run(store, started, datetime.now(timezone.utc), mode)
        manifest["month_minutes_used"] = usage["total_minutes"]
        manifest["this_run_minutes"] = usage["runs"][-1]["minutes_rounded_up"]
    store.save("inbox/manifest.json", manifest)
    report.manifest = manifest
    return report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="radar.run")
    ap.add_argument("--config", type=Path, default=Path("config"))
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--dry-run", action="store_true", help="fetch live but write only to --out")
    ap.add_argument("--out", type=Path, default=Path("dryrun_out"), help="dry-run output dir")
    ap.add_argument("--sources", help="comma-separated subset, e.g. greenhouse,hn")
    ap.add_argument("--commit-msg-file", type=Path, help="write the commit message here")
    args = ap.parse_args(argv)
    only = set(args.sources.split(",")) if args.sources else None

    if args.dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_data = Path(tmp) / "data"
            if args.data.exists():
                shutil.copytree(args.data, tmp_data)
            else:
                tmp_data.mkdir()
            report = run(args.config, tmp_data, dry_run=True, only_sources=only)
            if args.out.exists():
                shutil.rmtree(args.out)
            shutil.copytree(tmp_data / "inbox", args.out)
        print(f"dry-run: new={report.new_total} pending={report.pending_count} overflow={report.overflow} -> {args.out}")
        return 0

    report = run(args.config, args.data, only_sources=only)
    m = report.manifest
    msg = f"radar: {m.get('new_total', 0)} new, {m.get('pending_count', 0)} pending [skip ci]"
    print(msg)
    if args.commit_msg_file:
        args.commit_msg_file.write_text(msg, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
