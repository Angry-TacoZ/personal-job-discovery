from __future__ import annotations

import pytest

from job_discovery.adapters.ashby import AshbyAdapter
from job_discovery.adapters.base import AdapterError
from job_discovery.adapters.greenhouse import GreenhouseAdapter
from job_discovery.adapters.lever import LeverAdapter
from job_discovery.schemas import SourcePlatform


@pytest.mark.parametrize(
    ("platform", "adapter", "fixture_name", "expected_title", "expected_remote"),
    [
        (
            SourcePlatform.GREENHOUSE,
            GreenhouseAdapter(),
            "greenhouse.json",
            "AI Operations Analyst",
            "remote",
        ),
        (
            SourcePlatform.LEVER,
            LeverAdapter(),
            "lever.json",
            "Business Systems Analyst",
            "onsite",
        ),
        (
            SourcePlatform.ASHBY,
            AshbyAdapter(),
            "ashby.json",
            "AI Evaluation Coordinator",
            "remote",
        ),
    ],
)
def test_normalization_skips_malformed_listings(
    companies,
    load_fixture,
    platform,
    adapter,
    fixture_name,
    expected_title,
    expected_remote,
):
    result = adapter.normalize_payload(companies[platform], load_fixture(fixture_name))

    assert len(result.jobs) == 1
    assert result.jobs[0].title == expected_title
    assert result.jobs[0].remote_status == expected_remote
    assert "<" not in result.jobs[0].description
    assert len(result.warnings) == 1
    assert "Skipped malformed listing" in result.warnings[0]


@pytest.mark.parametrize(
    ("adapter", "payload"),
    [(GreenhouseAdapter(), []), (LeverAdapter(), {}), (AshbyAdapter(), {"jobs": {}})],
)
def test_malformed_top_level_payload_is_rejected(adapter, payload, companies):
    platform = {
        GreenhouseAdapter: SourcePlatform.GREENHOUSE,
        LeverAdapter: SourcePlatform.LEVER,
        AshbyAdapter: SourcePlatform.ASHBY,
    }[type(adapter)]
    with pytest.raises(AdapterError):
        adapter.normalize_payload(companies[platform], payload)

