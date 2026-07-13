from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from job_discovery.adapters import ADAPTERS
from job_discovery.adapters.base import BaseAdapter
from job_discovery.config import resolve_project_path
from job_discovery.reports import write_reports
from job_discovery.repository import Repository
from job_discovery.schemas import AppConfig
from job_discovery.scoring import score_job

logger = logging.getLogger(__name__)


async def run_scan(
    config: AppConfig,
    project_root: Path,
    repository: Repository,
    *,
    adapters: dict[Any, BaseAdapter] | None = None,
) -> dict[str, Any]:
    selected_adapters = adapters or ADAPTERS
    enabled = [company for company in config.companies if company.enabled]
    repository.sync_companies(config.companies)
    run_id = repository.start_scan(len(enabled))
    errors: list[dict[str, str]] = []
    warnings: dict[str, list[str]] = {}
    totals = {"succeeded": 0, "new": 0, "updated": 0, "inactive": 0}

    timeout = httpx.Timeout(config.request_timeout_seconds)
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    headers = {"User-Agent": config.user_agent, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=timeout, limits=limits, headers=headers) as client:
        for company in enabled:
            try:
                adapter = selected_adapters[company.ats_platform]
                result = await adapter.fetch(company, client, config.request_retries)
                if not result.jobs and result.warnings:
                    raise RuntimeError(
                        "all returned listings were malformed; preserving prior state"
                    )
                scored = [(job, score_job(job, config.scoring)) for job in result.jobs]
                new, updated, inactive = repository.upsert_source_jobs(
                    company, run_id, scored, result.warnings
                )
                totals["succeeded"] += 1
                totals["new"] += new
                totals["updated"] += updated
                totals["inactive"] += inactive
                if result.warnings:
                    warnings[company.company_name] = result.warnings
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                logger.exception("Source scan failed for %s", company.company_name)
                repository.record_source_failure(company, message)
                errors.append({"company": company.company_name, "message": message})

    status = "success" if not errors else ("partial" if totals["succeeded"] else "failed")
    repository.finish_scan(
        run_id,
        succeeded=totals["succeeded"],
        new_jobs=totals["new"],
        updated_jobs=totals["updated"],
        inactive_jobs=totals["inactive"],
        errors=errors,
    )
    report_jobs = repository.jobs_for_report(run_id, config.score_alert_threshold)
    markdown_path, json_path = write_reports(
        resolve_project_path(project_root, config.reports_dir),
        run_id=run_id,
        status=status,
        jobs=report_jobs,
        errors=errors,
        score_threshold=config.score_alert_threshold,
    )
    return {
        "run_id": run_id,
        "status": status,
        "companies_attempted": len(enabled),
        "companies_succeeded": totals["succeeded"],
        "new_jobs": totals["new"],
        "updated_jobs": totals["updated"],
        "inactive_jobs": totals["inactive"],
        "errors": errors,
        "warnings": warnings,
        "markdown_report": str(markdown_path),
        "json_report": str(json_path),
    }
