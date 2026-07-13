from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_discovery.config import load_config, resolve_project_path
from job_discovery.database import create_session_factory
from job_discovery.repository import Repository

_WEB_ROOT = Path(__file__).parent


def create_app(config_path: str | Path = "config/companies.yml") -> FastAPI:
    config, project_root = load_config(config_path)
    database_path = resolve_project_path(project_root, config.database_path)
    repository = Repository(create_session_factory(database_path))
    repository.sync_companies(config.companies)
    templates = Jinja2Templates(directory=str(_WEB_ROOT / "templates"))

    app = FastAPI(title="Personal Job Discovery", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(_WEB_ROOT / "static")), name="static")
    app.state.repository = repository

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
        minimum_score: int | None = Query(default=None, ge=-100, le=100),
        new_only: bool = False,
    ) -> HTMLResponse:
        jobs = repository.dashboard_jobs(
            company=company,
            title=title,
            source=source,
            location=location,
            remote_status=remote_status,
            minimum_score=minimum_score,
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
        job_id: int,
        status: str = Query(pattern="^(new|reviewed|ignored)$"),
    ) -> RedirectResponse:
        if not repository.set_review_status(job_id, status):
            raise HTTPException(status_code=404, detail="Job not found")
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    return app
