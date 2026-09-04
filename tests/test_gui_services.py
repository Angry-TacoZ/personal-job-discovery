from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from job_discovery.gui_services import ConfigStore, build_schedule_command
from job_discovery.schemas import CompanyConfig


def write_config(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        """
database_path: data/jobs.db
reports_dir: reports
request_timeout_seconds: 20
request_retries: 2
score_alert_threshold: 20
companies:
  - company_name: Existing
    ats_platform: greenhouse
    ats_identifier: existing
    enabled: true
    notes: Keep this note.
scoring:
  positive_rules:
    - reason: Testing
      phrases: [testing]
      weight: 5
  negative_rules: []
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_company_editor_preserves_scoring_and_settings(tmp_path: Path):
    config_path = tmp_path / "config" / "companies.yml"
    write_config(config_path)
    store = ConfigStore(config_path)

    store.save_company(
        CompanyConfig(
            company_name="New Company",
            ats_platform="lever",
            ats_identifier="new-company",
            notes="Added from GUI",
        )
    )
    store.save_settings(threshold=30, prune_below_score=40, timeout=15, retries=1)

    config = store.app_config()
    assert [company.company_name for company in config.companies] == [
        "Existing",
        "New Company",
    ]
    assert config.score_alert_threshold == 30
    assert config.prune_below_score == 40
    assert config.request_timeout_seconds == 15
    assert config.scoring.positive_rules[0].reason == "Testing"
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["database_path"] == "data/jobs.db"


def test_settings_store_preserves_enabled_and_disabled_pruning_states(tmp_path: Path):
    config_path = tmp_path / "config" / "companies.yml"
    write_config(config_path)
    store = ConfigStore(config_path)

    store.save_settings(threshold=30, prune_below_score=40, timeout=15, retries=1)
    assert store.app_config().prune_below_score == 40

    store.save_settings(threshold=30, prune_below_score=None, timeout=15, retries=1)
    assert store.app_config().prune_below_score is None
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["prune_below_score"] is None


def test_company_editor_rejects_duplicate_source_identifier(tmp_path: Path):
    config_path = tmp_path / "config" / "companies.yml"
    write_config(config_path)
    store = ConfigStore(config_path)

    with pytest.raises(ValidationError, match="must be unique"):
        store.save_company(
            CompanyConfig(
                company_name="Duplicate",
                ats_platform="greenhouse",
                ats_identifier="existing",
            )
        )

    assert len(store.companies()) == 1


def test_schedule_command_is_allowlisted_and_uses_absolute_paths(tmp_path: Path):
    config_path = tmp_path / "config" / "companies.yml"
    executable = tmp_path / ".venv" / "Scripts" / "python.exe"
    command = build_schedule_command(
        config_path,
        6,
        executable=executable,
        start_at=datetime(2026, 7, 13, 9, 5),
    )

    assert command[0] == "schtasks.exe"
    assert command[command.index("/MO") + 1] == "6"
    assert command[command.index("/ST") + 1] == "09:05"
    action = command[command.index("/TR") + 1]
    assert str(config_path.resolve()) in action
    assert "job_discovery" in action


def test_schedule_command_rejects_unapproved_interval(tmp_path: Path):
    with pytest.raises(ValueError, match="interval"):
        build_schedule_command(tmp_path / "config.yml", 5)
