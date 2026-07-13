from pathlib import Path

import pytest

from job_discovery.config import load_resume_profile
from job_discovery.schemas import AppConfig, CompanyConfig, ScoringConfig


def make_config(profile_path: str) -> AppConfig:
    return AppConfig(
        resume_profile_path=profile_path,
        companies=[
            CompanyConfig(
                company_name="Example",
                ats_platform="greenhouse",
                ats_identifier="example",
            )
        ],
        scoring=ScoringConfig(),
    )


def test_resume_profile_loads_only_from_inside_project(tmp_path: Path):
    profile = tmp_path / "config" / "resume-profile.local.yml"
    profile.parent.mkdir()
    profile.write_text(
        """
profile_label: Test profile
years_experience: 8
education_level: high_school
evidence_rules:
  - reason: Python
    phrases: [python]
    weight: 20
""".strip(),
        encoding="utf-8",
    )

    loaded = load_resume_profile(make_config("config/resume-profile.local.yml"), tmp_path)

    assert loaded is not None
    assert loaded.profile_label == "Test profile"
    assert loaded.evidence_rules[0].phrases == ["python"]


def test_resume_profile_rejects_path_outside_project(tmp_path: Path):
    outside = tmp_path.parent / "outside-profile.yml"
    outside.write_text("not: used", encoding="utf-8")

    with pytest.raises(ValueError, match="inside the project"):
        load_resume_profile(make_config(str(outside)), tmp_path)


def test_missing_ignored_profile_falls_back_without_reading_other_files(tmp_path: Path):
    loaded = load_resume_profile(
        make_config("config/resume-profile.local.yml"), tmp_path
    )

    assert loaded is None
