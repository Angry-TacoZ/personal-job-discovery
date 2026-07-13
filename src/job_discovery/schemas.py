from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class SourcePlatform(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class RemoteStatus(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class CompanyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str = Field(min_length=1, max_length=200)
    ats_platform: SourcePlatform
    ats_identifier: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    enabled: bool = True
    notes: str = Field(default="", max_length=1000)

    @field_validator("company_name", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ScoreRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=200)
    phrases: list[str] = Field(min_length=1)
    weight: int = Field(ge=-100, le=100)
    fields: list[str] = Field(
        default_factory=lambda: ["title", "description", "location", "department"]
    )

    @field_validator("phrases")
    @classmethod
    def normalize_phrases(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().casefold() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty phrase is required")
        return cleaned


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_score: int = -100
    maximum_score: int = 100
    positive_rules: list[ScoreRule] = Field(default_factory=list)
    negative_rules: list[ScoreRule] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_label: str = Field(default="Local resume profile", min_length=1, max_length=200)
    years_experience: int = Field(ge=0, le=60)
    education_level: str = Field(pattern=r"^(high_school|associate|bachelor|master|doctorate)$")
    active_clearance: bool = False
    work_authorization: str = Field(
        default="unknown", pattern=r"^(unknown|authorized|requires_sponsorship)$"
    )
    locations: list[str] = Field(default_factory=list)
    excluded_locations: list[str] = Field(default_factory=list)
    evidence_rules: list[ScoreRule] = Field(min_length=1)
    gap_rules: list[ScoreRule] = Field(default_factory=list)

    @field_validator("profile_label")
    @classmethod
    def strip_profile_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("locations", "excluded_locations")
    @classmethod
    def normalize_locations(cls, values: list[str]) -> list[str]:
        return [value.strip().casefold() for value in values if value.strip()]

    @model_validator(mode="after")
    def positive_evidence_weights(self) -> ResumeProfile:
        if any(rule.weight < 0 for rule in self.evidence_rules):
            raise ValueError("resume evidence weights cannot be negative")
        if any(rule.weight > 0 for rule in self.gap_rules):
            raise ValueError("resume gap weights cannot be positive")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_path: str = "data/jobs.db"
    reports_dir: str = "reports"
    user_agent: str = "PersonalJobDiscovery/0.1 (+local personal-use monitor)"
    request_timeout_seconds: float = Field(default=20, ge=1, le=60)
    request_retries: int = Field(default=2, ge=0, le=5)
    score_alert_threshold: int = Field(default=20, ge=-100, le=100)
    resume_profile_path: str | None = Field(default=None, max_length=500)
    companies: list[CompanyConfig]
    scoring: ScoringConfig

    @model_validator(mode="after")
    def unique_company_sources(self) -> AppConfig:
        keys = [(company.ats_platform, company.ats_identifier) for company in self.companies]
        if len(keys) != len(set(keys)):
            raise ValueError("company ATS platform and identifier combinations must be unique")
        return self


class NormalizedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_platform: SourcePlatform
    company_name: str = Field(min_length=1, max_length=200)
    company_identifier: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    external_job_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    location: str = Field(default="Unknown", max_length=500)
    remote_status: RemoteStatus = RemoteStatus.UNKNOWN
    employment_type: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=300)
    description: str = Field(default="", max_length=200_000)
    apply_url: HttpUrl
    source_url: HttpUrl
    date_posted: datetime | None = None

    @field_validator(
        "company_name",
        "external_job_id",
        "title",
        "location",
        "employment_type",
        "department",
        "description",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    def content_hash(self) -> str:
        fields = self.model_dump(mode="json", exclude={"date_posted"})
        canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FetchResult(BaseModel):
    jobs: list[NormalizedJob]
    warnings: list[str] = Field(default_factory=list)


class ScoreResult(BaseModel):
    score: int
    match_reasons: list[str]
    rejection_reasons: list[str]
    preference_score: int | None = None
    resume_score: int | None = None
    screening_score: int | None = None
    resume_reasons: list[str] = Field(default_factory=list)
    resume_gaps: list[str] = Field(default_factory=list)
    screening_reasons: list[str] = Field(default_factory=list)
    screening_flags: list[str] = Field(default_factory=list)
