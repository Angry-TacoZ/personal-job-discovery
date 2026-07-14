# Personal Job Discovery

A local-first, single-user job monitor for a curated list of public company career pages. It fetches Greenhouse, Lever, and Ashby listings, normalizes them into SQLite, detects additions and removals, applies explainable deterministic scoring, writes Markdown/JSON reports, and serves a local FastAPI review queue.

This is not a commercial job-board crawler. It does not bypass authentication or anti-bot controls, scrape private pages, apply to jobs, send paid notifications, or use an LLM.

## Use it without PowerShell

On Windows, double-click **`Start Job Discovery.cmd`** in the project folder. The first launch prepares the local environment automatically, then opens the local control center in your browser. Later launches open directly without a setup window.

The control center provides:

- **Overview** — scan now, see queue counts, and open the review dashboard
- **Companies** — add, edit, enable, disable, or remove monitored ATS boards
- **Skills scoring** — inspect the sanitized evidence profile and rescore saved jobs
- **Automation** — install or remove a Windows background scan schedule
- **Settings** — change the alert threshold, timeout, and retry count
- **Reports** — read or open the latest Markdown report

Python 3.12 or newer must be installed once on the computer. No PowerShell commands are required for normal use. The interface binds only to `127.0.0.1`, so it is available on this computer rather than the public internet.

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

## Manual developer setup

The double-click launcher handles normal setup. These commands are only for development or troubleshooting:

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

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The dashboard provides new and score-ranked jobs, company/source/remote/location/title/score filters, ascending or descending sorting by overall match, preference fit, skills match, or eligibility fit, source health, match explanations, direct apply links, and reviewed/ignored actions. The FAQ page explains each component and the limits of estimating a private ATS screen.

## Scoring

The overall 0-100 score combines three visible components:

- **25% preference fit** from the positive and negative rules in `config/companies.yml`
- **50% skills match** from documented capabilities in the ignored local resume profile
- **25% eligibility fit** from visible experience, education, location, clearance, seniority, and sponsorship language

A rule contributes its weight once when any configured phrase appears in one of its selected fields. Every component and matched phrase is stored with the job so the result remains auditable. Explicit likely blockers cap the overall score at 49 rather than being hidden inside an otherwise strong match.

`config/resume-profile.local.yml` is intentionally excluded from Git. It contains a sanitized evidence profile without the source PDF, phone number, or email. The raw resume remains outside the project. The configured profile path is restricted to the project directory to prevent the server from reading arbitrary files.

If the ignored profile is absent, such as on a GitHub-hosted runner, the scanner safely falls back to preference-only scoring instead of reading another file or failing the scan.

Phrase matching is deliberately literal and case-insensitive. Screening readiness is an estimate based only on visible posting language, not the employer's private ATS configuration. Degree equivalency, work authorization, and ambiguous requirements remain warnings for human review rather than invented facts.

## Tests and lint

Tests use saved provider fixtures and make no live network requests:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

Coverage includes all three normalizers, malformed provider data, duplicate/new detection, inactive handling, deterministic component scores, screening blockers, local-profile containment, database migration, and preservation of active jobs during source failures.

## Automatic scanning

### Recommended: Windows Task Scheduler

For one person on one Windows machine, Task Scheduler is the simplest trustworthy persistence model because `data/jobs.db` stays on the same disk.

Open **Automation** in the desktop app, choose a 3-, 6-, 12-, or 24-hour interval, and select **Install schedule**. The same screen can remove the task later. The task runs with normal user privileges and does not expose the dashboard publicly.

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
- The built-in migration currently covers the resume-score columns; broader future schema changes may need a formal migration framework.
- The interface is local-only, rejects nonlocal clients and cross-origin writes, and has no user accounts. Do not bind it to a public interface.
- GitHub Actions cache does not provide durable SQLite persistence.
- Workday, email, Slack, Discord, and webhook alerts are intentionally out of scope.

## Project layout

```text
config/                  Companies, preference weights, and ignored local resume profile
src/job_discovery/
  adapters/              Shared adapter interface and three ATS implementations
  database.py            SQLAlchemy models and SQLite setup
  repository.py          Persistence boundary and scan-state transitions
  scoring.py             Deterministic explainable rules
  scan.py                Fault-isolated orchestration
  reports.py             Markdown and JSON alert output
  gui.py                 Double-click launcher for the local browser GUI
  gui_services.py        Safe configuration and Task Scheduler boundaries
  web/                    FastAPI routes, escaped templates, and CSS
tests/fixtures/           Saved ATS responses (no live network in tests)
.github/workflows/        Optional scheduled scanner
Start Job Discovery.cmd  Double-click GUI launcher and first-run setup entry
```

## Best next feature

After several days of local scans, add a **configuration health command** that checks each company identifier, shows raw/valid/skipped listing counts, and previews score distribution without mutating SQLite. That will make onboarding new companies safer and reveal provider drift before it affects the review queue.
