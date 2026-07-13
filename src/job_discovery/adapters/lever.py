from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from job_discovery.adapters.base import AdapterError, BaseAdapter, warning
from job_discovery.schemas import CompanyConfig, FetchResult, NormalizedJob, SourcePlatform
from job_discovery.text import infer_remote_status


class _LeverCategories(BaseModel):
    model_config = ConfigDict(extra="ignore")
    location: str = ""
    team: str = ""
    department: str = ""
    commitment: str = ""


class _LeverJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    categories: _LeverCategories = Field(default_factory=_LeverCategories)
    descriptionPlain: str = ""
    additionalPlain: str = ""
    hostedUrl: str
    applyUrl: str
    createdAt: int | None = None


class LeverAdapter(BaseAdapter):
    def endpoint(self, company: CompanyConfig) -> str:
        return f"https://api.lever.co/v0/postings/{company.ats_identifier}?mode=json"

    def normalize_payload(self, company: CompanyConfig, payload: Any) -> FetchResult:
        if not isinstance(payload, list):
            raise AdapterError("Lever response was not a list")
        jobs: list[NormalizedJob] = []
        warnings: list[str] = []
        for index, item in enumerate(payload):
            try:
                raw = _LeverJob.model_validate(item)
                location = raw.categories.location or "Unknown"
                department = raw.categories.team or raw.categories.department or None
                posted = (
                    datetime.fromtimestamp(raw.createdAt / 1000, tz=UTC)
                    if raw.createdAt is not None
                    else None
                )
                jobs.append(
                    NormalizedJob(
                        source_platform=SourcePlatform.LEVER,
                        company_name=company.company_name,
                        company_identifier=company.ats_identifier,
                        external_job_id=raw.id,
                        title=raw.text,
                        location=location,
                        remote_status=infer_remote_status(location, raw.text),
                        employment_type=raw.categories.commitment or None,
                        department=department,
                        description="\n".join(
                            value for value in [raw.descriptionPlain, raw.additionalPlain] if value
                        ),
                        apply_url=raw.applyUrl,
                        source_url=raw.hostedUrl,
                        date_posted=posted,
                    )
                )
            except Exception as exc:
                warnings.append(warning(index, exc))
        return FetchResult(jobs=jobs, warnings=warnings)

