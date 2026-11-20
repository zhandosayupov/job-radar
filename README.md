# job-radar

A read-only job collection pipeline. It fetches postings from public job boards and
APIs, normalizes them, dedupes them, applies a deterministic prefilter, and writes a
compact **inbox** (`pending.jsonl` + `manifest.json`) for a separate LLM labeler to read
once a day. This repo makes no LLM calls.

**Two-repo layout.** This public repo holds only the engine: code, tests and example
config. The deployment is a separate **private** repo that holds `config/`, `data/`,
secrets and the scheduled workflow (see `examples/deploy/radar.yml`). That workflow
checks this repo out as `engine/`.

## Ground rules
- Read-only. The pipeline never applies, posts, messages or logs in to job boards.
- It respects `robots.txt` (RFC 9309) and site terms. A 403/429/captcha/robots refusal
  marks the source `blocked`. The pipeline never works around one: no proxies, no
  captcha solving, no headless tricks.
- One fixed User-Agent with a contact URL, at most 1 request/s per host, timeouts, and 2
  retries with backoff.
- Unknown values stay `null`. Salary, visa status and dates are never guessed.

## Local run
```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"     # Windows; use .venv/bin on Linux/macOS
python -m radar.run --config ../job-radar-ops/config --data ../job-radar-ops/data --dry-run
```
`--dry-run` fetches live data and runs the full pipeline on a **temporary copy** of
`data/`, then writes the resulting `pending.jsonl`, `manifest.json` and
`dropped_last.jsonl` to `./dryrun_out/`. The real `data/` is never touched, and no
alerts or usage records are sent or written.
Use `--sources greenhouse,hn` to run a subset.

Without `--config`, the packaged defaults in `radar/defaults/` are used.

## Pipeline
budget guard → label ingestion (labeled ids leave pending) → fetch (each source isolated;
one failing source never stops the run) → seen check → prefilter → cross-source dedupe
(ATS beats aggregator beats community) → closures → inbox cap (150 items / ~60k tokens,
newest first; the rest stays in `overflow.jsonl`) → manifest, health, alerts → usage.

## Adding a company or a source
- **Company on a supported ATS:** add one line to `config/companies.yaml`:
  `- {name: Acme, ats: greenhouse, token: acme, priority: 2, complete: true}`.
  Use `departments: [ids]` to fetch only some departments. `complete: true` means the
  board is fetched in full, so jobs that disappear are recorded in `closed.jsonl`.
- **New source:** add `radar/sources/<name>.py` that subclasses `Source` and returns a
  `FetchOutcome`, then register it in `radar/sources/__init__.py` and enable it in
  `sources.yaml`. Record fixtures (`tools/record_fixtures.py`) and add contract tests
  in `tests/test_sources.py`.

## Config
Files in `config/` are deep-merged over `radar/defaults/*.yaml`: dicts merge, while
lists and scalars replace. Keyword patterns: plain entries match whole words,
case-insensitive (`c++` works); `re:` is a case-insensitive regex; `RE:` is a
case-sensitive regex.

The prefilter rules, as counted in the manifest's `dropped_by_rule`:

| rule | meaning |
|---|---|
| `title_excluded` | intern / staff / principal / manager / head of / trader … (with exceptions such as "Trading Systems" and "Member of Technical Staff") |
| `title_not_eng` | not an engineering title |
| `text_excluded` | security clearance, US persons only, must already have the right to work, RU citizenship or residency |
| `no_sponsorship` | "no visa sponsorship" etc. on a job that is **not** remote-eligible (a remote job open to your region needs no visa) |
| `location_ru_by` | onsite or residency in Russia / Belarus |
| `location_na_au` | US / Canada / Australia without positive visa text |
| `location_other` | only regions outside the target list |

Each run writes the dropped items to `data/inbox/dropped_last.jsonl` with the rule and
the matched keyword, so you can audit the filters.

## Budget (GitHub Free: 2000 private-repo minutes/month)
Each run appends `{started, finished, minutes_rounded_up, mode}` to
`data/usage/<YYYY-MM>.json` and keeps `total_minutes` up to date. Minutes are counted from
the workflow's first step and rounded up, the same way GitHub bills them. Above
`budget.month_soft_limit` (1400) only tier-1 sources run. Above `month_hard_limit`
(1700) the run exits early with a notice in the manifest. Tests run in this public repo
and don't count against the limit.

## Source status (manifest `sources.<name>.status`)
| status | meaning | what to do |
|---|---|---|
| `ok` | every unit fetched | – |
| `partial` | some units failed (e.g. one Greenhouse board); see `detail` | check `detail`; usually one board token changed |
| `blocked` | 401/403/429, captcha or robots.txt refusal | don't work around it; disable the source or find an official API |
| `error` | 404, persistent 5xx, network or parser error | check `detail`; re-record fixtures and fix the parser |
| `skipped` | budget limit or run-time budget reached | – |

## Alerts (Telegram bot)
Set the `RADAR_TG_BOT_TOKEN` and `RADAR_TG_CHAT_ID` secrets in the private repo. Each
run compares every source with its previous run and sends one message when a source
**broke** (ok → blocked/error), **degraded** (ok → partial), went **empty** (ok with
items → 0 items, a silent breakage) or **recovered**. A failed workflow run also sends
an alert. Without the secrets, alerting is a no-op. Errors are reported without the
token-bearing URL.

## Tests
```bash
pytest                    # offline: unit + contract (recorded fixtures) + e2e, a few seconds
python tools/leak_guard.py
python tools/record_fixtures.py [greenhouse|hn|wwr]   # re-record when an API changes
```
To enable the pre-commit leak guard, run `git config core.hooksPath .githooks` once per
clone. See `docs/TESTING.md` for the manual checklist.

## Rotating secrets
Secrets live only in the private repo's GitHub Actions secrets. To rotate the bot
token, use BotFather `/revoke`, then update `RADAR_TG_BOT_TOKEN`. Telegram userbot
secrets (Phase 2) will be documented here when that source is enabled.
