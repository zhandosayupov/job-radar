# Testing

Three layers, from cheapest to most realistic.

## 1. Automatic (every push, in CI)
`.github/workflows/tests.yml` runs these on every push and pull request. They are
offline and finish in seconds.

| suite | what it proves |
|---|---|
| `test_text.py`, `test_models.py` | HTML stripping, URL canonicalization, visa/salary evidence, excerpt priority, null-not-guess |
| `test_prefilter.py` | each rule has drop **and** keep cases (EN + RU) |
| `test_core_state.py` | dedupe preference, inbox caps, seen/labels/closed files, budget modes and rounding |
| `test_health_alerts.py` | broke/degraded/empty/recovered transitions; the alert never leaks the token |
| `test_http.py` | robots.txt, 403/429 → blocked, retries with backoff, 1 req/s throttle, challenge pages |
| `test_sources.py` | parsers against **recorded live responses** (contract tests) |
| `test_run_e2e.py` | full pipeline twice: idempotent, labels remove ids, closures, failure isolation, budget guard, dry-run leaves data untouched |
| `test_leak_guard.py` | the public repo contains no secrets or private paths |

## 2. Live (each scheduled run = a smoke test)
Every run in the private repo is also a live check. The manifest shows status and
counts per source. Health alerts go to Telegram when a source breaks, degrades, goes
silently empty or recovers. A source failing 3 runs in a row is listed in
`manifest.persistent_failures`.

When an alert says a source broke:
1. Read `detail` in the manifest (or the run log).
2. Run `python tools/record_fixtures.py <source>` locally, then `pytest tests/test_sources.py`.
   The failing assertions show what changed.
3. Fix the parser, commit the new fixture, and push. CI must be green before the next run picks it up.

## 3. Manual (at checkpoints)
```bash
python -m radar.run --config ../job-radar-ops/config --data ../job-radar-ops/data --dry-run
```
Then check `dryrun_out/`:
- `manifest.json`: every source is `ok`, or not ok with an explained `detail`; `dropped_by_rule` looks plausible.
- `pending.jsonl`: read 10 random items. Each should be an engineering role in a target region, or remote. Check `url` opens the posting. `visa_text` and `salary_text`, when present, should be verbatim from the posting.
- `dropped_last.jsonl`: scan for good roles that were dropped wrongly (wrong rule or `hit`). Fix the keyword lists and add a test case to `test_prefilter.py`.
- Run it twice: the second run must show `new=0`.
