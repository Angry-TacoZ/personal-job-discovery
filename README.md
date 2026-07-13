# Personal Job Discovery

A local-first, single-user job monitor for a curated list of public company career pages. It fetches Greenhouse, Lever, and Ashby listings, normalizes them into SQLite, detects additions and removals, applies explainable deterministic scoring, writes Markdown/JSON reports, and serves a local FastAPI review queue.

This is not a commercial job-board crawler. It does not bypass authentication or anti-bot controls, scrape private pages, apply to jobs, send paid notifications, or use an LLM.

## Architecture

```text
config/companies.yml
        │
        ▼
Greenhouse / Lever / Ashby adapters ── fixed public JSON endpoints
        │                                timeout + bounded retries
        ▼
Pydantic validation and normalization ── malformed rows are skipped and logged
        │
        ▼
Deterministic weighted scorer ────────── match and rejection explanations
        │
        ▼
SQLite repository ────────────────────── deduplication, first/last seen, active state
        ├── reports/latest.md + latest.json
        └── FastAPI + escaped Jinja templates
```

Each ATS implements the same adapter contract. Source failures are isolated: one company can fail without stopping the rest. Jobs are marked inactive only after a successful scan of that source; temporary fetch failures preserve the previous active set.

Trust boundaries are intentionally small:

- Public ATS responses are untrusted and validated before persistence.
- ATS identifiers are allow-listed to letters, numbers, `_`, and `-`, then inserted only into fixed provider URLs.
- Job descriptions are stored as text and rendered through Jinja auto-escaping. They are never executed.
- Review/ignore actions accept only an allow-listed state and the server binds to `127.0.0.1` by default.
- No secrets or paid APIs are used. The application has no authentication because it is designed for localhost only; do not expose it publicly.

## Windows setup

Requirements: Git and Python 3.12 or newer. From PowerShell:

```powershell
cd C:\path\to\personal-job-discovery
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m job_discovery --config config/companies.yml init-db
```

If PowerShell blocks activation, activation is optional; replace `python` with `.\.venv\Scripts\python.exe` in subsequent commands.

## Configure target companies

Edit [`config/companies.yml`](config/companies.yml). The included GitLab, Palantir, and Notion entries are replaceable examples for Greenhouse, Lever, and Ashby. An ATS identifier is the board slug in the public career URL, not necessarily the company domain.

```yaml
companies:
  - company_name: Example Company
    ats_platform: greenhouse  # greenhouse, lever, or ashby
    ats_identifier: example-company
    enabled: true
    notes: Why this company is monitored.
```

Disable an entry with `enabled: false`. A disabled entry remains visible in the local company table but is not fetched.

Public endpoint shapes used:

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true`
- Lever: `https://api.lever.co/v0/postings/{identifier}?mode=json`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{identifier}`

Do not add Workday or private/authenticated sources to this version.

## Run a scan

```powershell
cd C:\path\to\personal-job-discovery
.\.venv\Scripts\python.exe -m job_discovery --config config/companies.yml scan
```

The command prints a JSON summary and updates:

- `data/jobs.db` — local state and review history
- `reports/latest.md` — readable alert report
- `reports/latest.json` — machine-readable alert report

The report includes newly discovered jobs and all active jobs at or above `score_alert_threshold`.

## Launch the dashboard

```powershell
cd C:\path\to\personal-job-discovery
.\.venv\Scripts\python.exe -m job_discovery --config config/companies.yml serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The dashboard provides new and score-ranked jobs, company/source/remote/location/title/score filters, source health, match explanations, direct apply links, and reviewed/ignored actions.

## Scoring

Scoring rules live entirely in `config/companies.yml`; application code contains no role-specific weights. A rule contributes its weight once when any configured phrase appears in one of its selected fields. Results are clamped to the configured minimum and maximum.

Positive and negative explanations are stored with every job so a score remains auditable. Phrase matching is deliberately simple and case-insensitive. It does not infer synonyms, years of experience, or whether a requirement is optional. Review rules after real scans and add precise phrases rather than broad words that create false positives.

## Tests and lint

Tests use saved provider fixtures and make no live network requests:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

Coverage includes all three normalizers, malformed provider data, duplicate/new detection, inactive handling, deterministic positive/negative explanations, and preservation of active jobs during source failures.

## Automatic scanning

### Recommended: Windows Task Scheduler

For one person on one Windows machine, Task Scheduler is the simplest trustworthy persistence model because `data/jobs.db` stays on the same disk.

After setup, test the wrapper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-scan.ps1
```

Then create a task from an elevated PowerShell prompt (change the path first):

```powershell
$project = 'C:\path\to\personal-job-discovery'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$project\scripts\run-scan.ps1`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 6) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName 'Personal Job Discovery Scan' -Action $action -Trigger $trigger -Description 'Scan configured public ATS job boards every six hours'
```

### GitHub Actions

`.github/workflows/scheduled-scan.yml` runs four times daily and can be started manually. It restores the newest matching Actions cache, scans, saves a new cache, and uploads the reports plus a database snapshot for 14 days.

Important limitation: GitHub-hosted runners are ephemeral. Actions cache is best-effort, immutable, evictable, and not a database backup. A cache miss makes existing listings appear new again. Concurrent scans are disabled, but state continuity is still weaker than Task Scheduler. Keep the repository private if the configuration, review state, or report is personal. Do not commit `data/jobs.db` to Git.

## Legal and operational boundaries

- Use only public, anonymous career-board endpoints provided by the ATS or company.
- Respect provider terms, rate limits, robots policies where applicable, and company requests.
- The scanner uses sequential company requests, a descriptive user agent, timeouts, and bounded retries.
- Never bypass login, CAPTCHA, access controls, or anti-bot measures.
- Do not execute or trust job-description content.
- Verify important listing details on the original page before applying.
- Provider endpoints can change without notice; failures are logged on the dashboard and reports.

## Known limitations

- Phrase scoring is transparent but literal; it can miss synonyms and misunderstand negation.
- Remote status is inferred from source fields and may be wrong when a provider omits structured data.
- Greenhouse's public feed often exposes an update timestamp rather than the original publication date.
- A listing that changes external ID is treated as new.
- Disappearance after a successful empty response marks prior jobs inactive; malformed-all responses are treated as failures to reduce false deactivation.
- There is no migration framework yet; schema changes during early development may require deleting the local database.
- The dashboard is local-only and has no authentication or CSRF protection. Do not bind it to a public interface.
- GitHub Actions cache does not provide durable SQLite persistence.
- Workday, email, Slack, Discord, and webhook alerts are intentionally out of scope.

## Project layout

```text
config/                  Human-editable companies and scoring weights
src/job_discovery/
  adapters/              Shared adapter interface and three ATS implementations
  database.py            SQLAlchemy models and SQLite setup
  repository.py          Persistence boundary and scan-state transitions
  scoring.py             Deterministic explainable rules
  scan.py                Fault-isolated orchestration
  reports.py             Markdown and JSON alert output
  web/                    FastAPI routes, escaped templates, and CSS
tests/fixtures/           Saved ATS responses (no live network in tests)
.github/workflows/        Optional scheduled scanner
```

## Best next feature

After several days of local scans, add a **configuration health command** that checks each company identifier, shows raw/valid/skipped listing counts, and previews score distribution without mutating SQLite. That will make onboarding new companies safer and reveal provider drift before it affects the review queue.
