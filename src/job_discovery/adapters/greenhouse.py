from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from job_discovery.adapters.base import AdapterError, BaseAdapter, warning
from job_discovery.schemas import CompanyConfig, FetchResult, NormalizedJob, SourcePlatform
from job_discovery.text import html_to_text, infer_remote_status


class _Name(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""


class _GreenhouseJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int | str
    title: str = Field(min_length=1)
    location: _Name | None = None
    departments: list[_Name] = Field(default_factory=list)
    content: str = ""
    absolute_url: str
    updated_at: datetime | None = None


class GreenhouseAdapter(BaseAdapter):
    def endpoint(self, company: CompanyConfig) -> str:
        return (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{company.ats_identifier}/jobs?content=true"
        )

    def normalize_payload(self, company: CompanyConfig, payload: Any) -> FetchResult:
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise AdapterError("Greenhouse response did not contain a jobs list")
        jobs: list[NormalizedJob] = []
        warnings: list[str] = []
        for index, item in enumerate(payload["jobs"]):
            try:
                raw = _GreenhouseJob.model_validate(item)
                location = raw.location.name if raw.location and raw.location.name else "Unknown"
                department = ", ".join(d.name for d in raw.departments if d.name) or None
                jobs.append(
                    NormalizedJob(
                        source_platform=SourcePlatform.GREENHOUSE,
                        company_name=company.company_name,
                        company_identifier=company.ats_identifier,
                        external_job_id=str(raw.id),
                        title=raw.title,
                        location=location,
                        remote_status=infer_remote_status(location, raw.title),
                        department=department,
                        description=html_to_text(raw.content),
                        apply_url=raw.absolute_url,
                        source_url=raw.absolute_url,
                        date_posted=raw.updated_at,
                    )
                )
            except Exception as exc:
                warnings.append(warning(index, exc))
        return FetchResult(jobs=jobs, warnings=warnings)

