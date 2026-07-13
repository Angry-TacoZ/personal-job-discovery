from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from job_discovery.adapters.base import AdapterError, BaseAdapter, warning
from job_discovery.schemas import CompanyConfig, FetchResult, NormalizedJob, SourcePlatform
from job_discovery.text import html_to_text, infer_remote_status


class _AshbyJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    location: str = ""
    employmentType: str | None = None
    department: str | None = None
    team: str | None = None
    descriptionPlain: str = ""
    descriptionHtml: str = ""
    jobUrl: str
    applyUrl: str
    publishedAt: datetime | None = None
    isRemote: bool | None = None


class AshbyAdapter(BaseAdapter):
    def endpoint(self, company: CompanyConfig) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{company.ats_identifier}"

    def normalize_payload(self, company: CompanyConfig, payload: Any) -> FetchResult:
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise AdapterError("Ashby response did not contain a jobs list")
        jobs: list[NormalizedJob] = []
        warnings: list[str] = []
        for index, item in enumerate(payload["jobs"]):
            try:
                raw = _AshbyJob.model_validate(item)
                location = raw.location or "Unknown"
                remote_status = (
                    "remote" if raw.isRemote else infer_remote_status(location, raw.title)
                )
                jobs.append(
                    NormalizedJob(
                        source_platform=SourcePlatform.ASHBY,
                        company_name=company.company_name,
                        company_identifier=company.ats_identifier,
                        external_job_id=raw.id,
                        title=raw.title,
                        location=location,
                        remote_status=remote_status,
                        employment_type=raw.employmentType,
                        department=raw.department or raw.team,
                        description=raw.descriptionPlain or html_to_text(raw.descriptionHtml),
                        apply_url=raw.applyUrl,
                        source_url=raw.jobUrl,
                        date_posted=raw.publishedAt,
                    )
                )
            except Exception as exc:
                warnings.append(warning(index, exc))
        return FetchResult(jobs=jobs, warnings=warnings)
