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
    ScoreResult,
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


@pytest.mark.asyncio
async def test_successful_scan_prunes_below_cutoff_and_keeps_boundary(
    tmp_path: Path, setup, monkeypatch
):
    config, repository = setup
    config.prune_below_score = 40
    adapter = QueueAdapter(
        [FetchResult(jobs=[make_job("low"), make_job("boundary"), make_job("high")])]
    )
    scores = {"low": 39, "boundary": 40, "high": 75}

    def fixed_score(job, _scoring, _profile):
        return ScoreResult(
            score=scores[job.external_job_id], match_reasons=[], rejection_reasons=[]
        )

    monkeypatch.setattr("job_discovery.scan.score_job", fixed_score)

    result = await run_scan(
        config, tmp_path, repository, adapters={SourcePlatform.GREENHOUSE: adapter}
    )

    assert result["pruned_jobs"] == 1
    assert result["new_jobs"] == 2
    assert {job.external_job_id for job in repository.dashboard_jobs()} == {
        "boundary",
        "high",
    }
    assert repository.latest_scan().pruned_jobs == 1


@pytest.mark.asyncio
async def test_failed_scan_does_not_prune_existing_low_score(tmp_path: Path, setup):
    config, repository = setup
    company = config.companies[0]
    repository.sync_companies([company])
    run_id = repository.start_scan(1)
    repository.upsert_source_jobs(
        company,
        run_id,
        [(make_job("low"), ScoreResult(score=20, match_reasons=[], rejection_reasons=[]))],
        [],
    )
    config.prune_below_score = 40

    failed = await run_scan(
        config,
        tmp_path,
        repository,
        adapters={SourcePlatform.GREENHOUSE: QueueAdapter([RuntimeError("temporary failure")])},
    )

    assert failed["status"] == "failed"
    assert failed["pruned_jobs"] == 0
    assert [job.external_job_id for job in repository.dashboard_jobs()] == ["low"]


def test_manual_prune_is_strictly_below_threshold(tmp_path: Path):
    company = CompanyConfig(
        company_name="Example", ats_platform="greenhouse", ats_identifier="example"
    )
    repository = Repository(create_session_factory(tmp_path / "jobs.db"))
    repository.sync_companies([company])
    run_id = repository.start_scan(1)
    repository.upsert_source_jobs(
        company,
        run_id,
        [
            (make_job("low"), ScoreResult(score=39, match_reasons=[], rejection_reasons=[])),
            (make_job("boundary"), ScoreResult(score=40, match_reasons=[], rejection_reasons=[])),
        ],
        [],
    )

    assert repository.prune_jobs_below(40) == 1
    assert [job.external_job_id for job in repository.dashboard_jobs()] == ["boundary"]


def test_dashboard_sorts_each_score_component_in_both_directions(tmp_path: Path):
    company = CompanyConfig(
        company_name="Example",
        ats_platform="greenhouse",
        ats_identifier="example",
    )
    repository = Repository(create_session_factory(tmp_path / "jobs.db"))
    repository.sync_companies([company])
    run_id = repository.start_scan(1)
    component_scores = {
        "a": ScoreResult(
            score=45,
            preference_score=80,
            resume_score=20,
            screening_score=50,
            match_reasons=[],
            rejection_reasons=[],
        ),
        "b": ScoreResult(
            score=60,
            preference_score=10,
            resume_score=90,
            screening_score=40,
            match_reasons=[],
            rejection_reasons=[],
        ),
        "c": ScoreResult(score=30, match_reasons=[], rejection_reasons=[]),
    }
    repository.upsert_source_jobs(
        company,
        run_id,
        [(make_job(job_id), score) for job_id, score in component_scores.items()],
        [],
    )

    expected = {
        ("preference", "asc"): ["b", "a", "c"],
        ("preference", "desc"): ["a", "b", "c"],
        ("resume", "asc"): ["a", "b", "c"],
        ("resume", "desc"): ["b", "a", "c"],
        ("screen", "asc"): ["b", "a", "c"],
        ("screen", "desc"): ["a", "b", "c"],
    }
    for (sort_by, direction), job_ids in expected.items():
        jobs = repository.dashboard_jobs(sort_by=sort_by, sort_direction=direction)
        assert [job.external_job_id for job in jobs] == job_ids


def test_dashboard_rejects_unknown_sort_values(tmp_path: Path):
    repository = Repository(create_session_factory(tmp_path / "jobs.db"))

    with pytest.raises(ValueError, match="sort field"):
        repository.dashboard_jobs(sort_by="title")
    with pytest.raises(ValueError, match="sort direction"):
        repository.dashboard_jobs(sort_direction="sideways")
