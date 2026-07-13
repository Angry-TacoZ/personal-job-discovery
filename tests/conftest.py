from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from job_discovery.schemas import CompanyConfig, SourcePlatform

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def companies() -> dict[SourcePlatform, CompanyConfig]:
    return {
        SourcePlatform.GREENHOUSE: CompanyConfig(
            company_name="Example Greenhouse",
            ats_platform="greenhouse",
            ats_identifier="example-gh",
        ),
        SourcePlatform.LEVER: CompanyConfig(
            company_name="Example Lever",
            ats_platform="lever",
            ats_identifier="example-lever",
        ),
        SourcePlatform.ASHBY: CompanyConfig(
            company_name="Example Ashby",
            ats_platform="ashby",
            ats_identifier="example-ashby",
        ),
    }

