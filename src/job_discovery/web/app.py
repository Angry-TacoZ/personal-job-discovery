from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_discovery.config import load_config, resolve_project_path
from job_discovery.database import create_session_factory
from job_discovery.gui_services import ALLOWED_INTERVALS, ConfigStore, TaskScheduler
from job_discovery.repository import Repository
from job_discovery.scan import run_scan
from job_discovery.schemas import CompanyConfig

_WEB_ROOT = Path(__file__).parent
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_LOCAL_ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000", "http://testserver"}


def _require_local_write(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in _LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="Local requests only")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in _LOCAL_ORIGINS:
        raise HTTPException(status_code=403, detail="Untrusted request origin")


def _redirect(path: str, message: str, *, error: bool = False) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    kind = "error" if error else "message"
    return RedirectResponse(
        url=f"{path}{separator}{kind}={quote_plus(message[:800])}", status_code=303
    )


def _friendly_error(exc: Exception) -> str:
    return str(exc)[:800] or type(exc).__name__


def _parse_optional_score(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        score = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Minimum score must be a whole number."
        ) from exc
    if not -100 <= score <= 100:
        raise HTTPException(
            status_code=422, detail="Minimum score must be between -100 and 100."
        )
    return score


def create_app(config_path: str | Path = "config/companies.yml") -> FastAPI:
    resolved_config_path = Path(config_path).expanduser().resolve()
    config, project_root = load_config(resolved_config_path)
    database_path = resolve_project_path(project_root, config.database_path)
    repository = Repository(create_session_factory(database_path))
    repository.sync_companies(config.companies)
    store = ConfigStore(resolved_config_path)
    scheduler = TaskScheduler(resolved_config_path)
    templates = Jinja2Templates(directory=str(_WEB_ROOT / "templates"))

    app = FastAPI(title="Personal Job Discovery", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_WEB_ROOT / "static")), name="static")
    app.state.repository = repository
    app.state.config_store = store
    app.state.scheduler = scheduler
    app.state.scan_lock = asyncio.Lock()

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "jobs": repository.count_jobs()}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        company: str | None = None,
        title: str | None = Query(default=None, max_length=200),
        source: str | None = None,
        location: str | None = Query(default=None, max_length=200),
        remote_status: str | None = None,
        minimum_score: str | None = Query(default=None, max_length=4),
        new_only: bool = False,
    ) -> HTMLResponse:
        parsed_minimum_score = _parse_optional_score(minimum_score)
        jobs = repository.dashboard_jobs(
            company=company,
            title=title,
            source=source,
            location=location,
            remote_status=remote_status,
            minimum_score=parsed_minimum_score,
            new_only=new_only,
        )
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "jobs": jobs,
                "companies": repository.companies(),
                "latest_scan": repository.latest_scan(),
                "options": repository.filter_options(),
                "filters": request.query_params,
            },
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int) -> HTMLResponse:
        job = repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return templates.TemplateResponse(request, "job_detail.html", {"job": job})

    @app.post("/jobs/{job_id}/status")
    def update_status(
        request: Request,
        job_id: int,
        status: str = Query(pattern="^(new|reviewed|ignored)$"),
    ) -> RedirectResponse:
        _require_local_write(request)
        if not repository.set_review_status(job_id, status):
            raise HTTPException(status_code=404, detail="Job not found")
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.get("/control", response_class=HTMLResponse)
    def control_center(
        request: Request, message: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        current = store.app_config()
        repository.sync_companies(current.companies)
        return templates.TemplateResponse(
            request,
            "control.html",
            {
                "config": current,
                "counts": repository.summary_counts(current.score_alert_threshold),
                "latest_scan": repository.latest_scan(),
                "companies": repository.companies(),
                "message": message,
                "error": error,
            },
        )

    @app.post("/control/scan")
    async def control_scan(request: Request) -> RedirectResponse:
        _require_local_write(request)
        if app.state.scan_lock.locked():
            return _redirect("/control", "A scan is already running.", error=True)
        try:
            async with app.state.scan_lock:
                current, root = load_config(resolved_config_path)
                summary = await run_scan(current, root, repository)
            return _redirect(
                "/control",
                f"Scan complete: {summary['new_jobs']} new jobs and "
                f"{len(summary['errors'])} source errors.",
                error=summary["status"] == "failed",
            )
        except Exception as exc:
            return _redirect("/control", _friendly_error(exc), error=True)

    @app.get("/control/companies", response_class=HTMLResponse)
    def company_manager(
        request: Request,
        edit: int | None = Query(default=None, ge=0),
        message: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        companies = store.companies()
        selected = companies[edit] if edit is not None and edit < len(companies) else None
        return templates.TemplateResponse(
            request,
            "companies.html",
            {
                "companies": companies,
                "selected": selected,
                "selected_index": edit if selected else None,
                "message": message,
                "error": error,
            },
        )

    @app.post("/control/companies/save")
    def save_company(
        request: Request,
        company_name: str = Form(min_length=1, max_length=200),
        ats_platform: str = Form(pattern="^(greenhouse|lever|ashby)$"),
        ats_identifier: str = Form(min_length=1, max_length=100),
        notes: str = Form(default="", max_length=1000),
        enabled: str | None = Form(default=None),
        index: str = Form(default=""),
    ) -> RedirectResponse:
        _require_local_write(request)
        try:
            company = CompanyConfig(
                company_name=company_name,
                ats_platform=ats_platform,
                ats_identifier=ats_identifier,
                notes=notes,
                enabled=enabled == "on",
            )
            selected_index = int(index) if index else None
            store.save_company(company, selected_index)
            repository.sync_companies(store.companies())
            return _redirect("/control/companies", f"Saved {company.company_name}.")
        except Exception as exc:
            return _redirect("/control/companies", _friendly_error(exc), error=True)

    @app.post("/control/companies/{index}/delete")
    def delete_company(request: Request, index: int) -> RedirectResponse:
        _require_local_write(request)
        try:
            store.delete_company(index)
            return _redirect("/control/companies", "Company removed.")
        except Exception as exc:
            return _redirect("/control/companies", _friendly_error(exc), error=True)

    @app.get("/control/automation", response_class=HTMLResponse)
    def automation(
        request: Request, message: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "automation.html",
            {
                "installed": scheduler.is_installed(),
                "intervals": sorted(ALLOWED_INTERVALS),
                "message": message,
                "error": error,
            },
        )

    @app.post("/control/automation/install")
    def install_automation(
        request: Request, interval: int = Form()
    ) -> RedirectResponse:
        _require_local_write(request)
        try:
            scheduler.install(interval)
            return _redirect("/control/automation", f"Scanning scheduled every {interval} hours.")
        except Exception as exc:
            return _redirect("/control/automation", _friendly_error(exc), error=True)

    @app.post("/control/automation/remove")
    def remove_automation(request: Request) -> RedirectResponse:
        _require_local_write(request)
        try:
            if scheduler.is_installed():
                scheduler.remove()
            return _redirect("/control/automation", "Automatic scanning removed.")
        except Exception as exc:
            return _redirect("/control/automation", _friendly_error(exc), error=True)

    @app.get("/control/settings", response_class=HTMLResponse)
    def settings(
        request: Request, message: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "settings.html",
            {"config": store.app_config(), "message": message, "error": error},
        )

    @app.post("/control/settings")
    def save_settings(
        request: Request,
        threshold: int = Form(ge=-100, le=100),
        timeout: float = Form(ge=1, le=60),
        retries: int = Form(ge=0, le=5),
    ) -> RedirectResponse:
        _require_local_write(request)
        try:
            store.save_settings(threshold, timeout, retries)
            return _redirect("/control/settings", "Settings saved.")
        except Exception as exc:
            return _redirect("/control/settings", _friendly_error(exc), error=True)

    @app.get("/control/reports", response_class=HTMLResponse)
    def reports(
        request: Request, message: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        current = store.app_config()
        path = resolve_project_path(project_root, current.reports_dir) / "latest.md"
        content = (
            path.read_text(encoding="utf-8")
            if path.is_file()
            else "No report yet. Run a scan first."
        )
        return templates.TemplateResponse(
            request,
            "report.html",
            {"report": content, "message": message, "error": error},
        )

    return app
