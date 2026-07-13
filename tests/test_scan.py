from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

import pytest

from job_discovery.adapters.base import BaseAdapter
from job_discovery.database import create_session_factory
from job_discovery.repository import Repository
from job_discovery.scan import run_scan
from job_discovery.schemas import (
    AppConfig,
    CompanyConfig,
    FetchResult,
    NormalizedJob,
    ScoringConfig,
    SourcePlatform,
)


class QueueAdapter(BaseAdapter):
    def __init__(self, outcomes: list[FetchResult | Exception]) -> None:
        self.outcomes = deque(outcomes)

    def endpoint(self, company: CompanyConfig) -> str:
        return "https://example.com"

    def normalize_payload(self, company: CompanyConfig, payload: Any) -> FetchResult:
        raise NotImplementedError

    async def fetch(self, company, client, retries):
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_job(external_id: str) -> NormalizedJob:
    return NormalizedJob(
        source_platform="greenhouse",
        company_name="Example",
        company_identifier="example",
        external_job_id=external_id,
        title=f"Analyst {external_id}",
        location="Remote",
        description="Testing and workflow automation",
        apply_url=f"https://example.com/{external_id}/apply",
        source_url=f"https://example.com/{external_id}",
    )


@pytest.fixture
def setup(tmp_path: Path):
    company = CompanyConfig(
        company_name="Example",
        ats_platform="greenhouse",
        ats_identifier="example",
    )
    config = AppConfig(companies=[company], scoring=ScoringConfig(), reports_dir="reports")
    repository = Repository(create_session_factory(tmp_path / "data" / "jobs.db"))
    return config, repository


@pytest.mark.asyncio
async def test_duplicate_new_and_inactive_detection(tmp_path: Path, setup):
    config, repository = setup
    adapter = QueueAdapter(
        [
            FetchResult(jobs=[make_job("a"), make_job("b")]),
            FetchResult(jobs=[make_job("b"), make_job("c")]),
        ]
    )
    adapters = {SourcePlatform.GREENHOUSE: adapter}

    first = await run_scan(config, tmp_path, repository, adapters=adapters)
    second = await run_scan(config, tmp_path, repository, adapters=adapters)

    assert first["new_jobs"] == 2
    assert second["new_jobs"] == 1
    assert second["updated_jobs"] == 1
    assert second["inactive_jobs"] == 1
    assert repository.count_jobs() == 3
    assert {job.external_job_id for job in repository.dashboard_jobs()} == {"b", "c"}


@pytest.mark.asyncio
async def test_temporary_failure_preserves_active_jobs(tmp_path: Path, setup):
    config, repository = setup
    adapter = QueueAdapter(
        [FetchResult(jobs=[make_job("a")]), RuntimeError("temporary upstream failure")]
    )
    adapters = {SourcePlatform.GREENHOUSE: adapter}

    await run_scan(config, tmp_path, repository, adapters=adapters)
    failed = await run_scan(config, tmp_path, repository, adapters=adapters)

    assert failed["status"] == "failed"
    assert failed["inactive_jobs"] == 0
    assert [job.external_job_id for job in repository.dashboard_jobs()] == ["a"]


@pytest.mark.asyncio
async def test_all_malformed_response_preserves_active_jobs(tmp_path: Path, setup):
    config, repository = setup
    adapter = QueueAdapter(
        [
            FetchResult(jobs=[make_job("a")]),
            FetchResult(jobs=[], warnings=["bad row"]),
        ]
    )
    adapters = {SourcePlatform.GREENHOUSE: adapter}

    await run_scan(config, tmp_path, repository, adapters=adapters)
    failed = await run_scan(config, tmp_path, repository, adapters=adapters)

    assert failed["status"] == "failed"
    assert repository.dashboard_jobs()[0].active is True
