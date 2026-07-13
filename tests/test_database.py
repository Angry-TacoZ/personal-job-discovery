from pathlib import Path

from sqlalchemy import inspect, text

from job_discovery.database import create_session_factory, database_engine
from job_discovery.repository import Repository
from job_discovery.schemas import ScoreRule, ScoringConfig


def test_existing_database_gains_resume_score_columns(tmp_path: Path):
    database_path = tmp_path / "jobs.db"
    first_factory = create_session_factory(database_path)
    first_engine = database_engine(first_factory)
    score_columns = {
        "preference_score",
        "resume_score",
        "screening_score",
        "resume_reasons",
        "resume_gaps",
        "screening_reasons",
        "screening_flags",
    }
    with first_engine.begin() as connection:
        for column in score_columns:
            connection.execute(text(f"ALTER TABLE jobs DROP COLUMN {column}"))
    first_engine.dispose()

    migrated_factory = create_session_factory(database_path)
    migrated_columns = {
        column["name"] for column in inspect(database_engine(migrated_factory)).get_columns("jobs")
    }

    assert score_columns <= migrated_columns


def test_unchanged_scoring_signature_skips_repeat_rescore(tmp_path: Path, monkeypatch):
    repository = Repository(create_session_factory(tmp_path / "jobs.db"))
    scoring = ScoringConfig(
        positive_rules=[ScoreRule(reason="Remote", phrases=["remote"], weight=10)]
    )
    assert repository.ensure_scores(scoring, None) == 0

    def unexpected_rescore(*_args):
        raise AssertionError("unchanged rubric should not rescore")

    monkeypatch.setattr(repository, "rescore_jobs", unexpected_rescore)

    assert repository.ensure_scores(scoring, None) == 0
