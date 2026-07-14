from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    companies_attempted: Mapped[int] = mapped_column(Integer, default=0)
    companies_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    updated_jobs: Mapped[int] = mapped_column(Integer, default=0)
    inactive_jobs: Mapped[int] = mapped_column(Integer, default=0)
    pruned_jobs: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)

    jobs: Mapped[list[JobRecord]] = relationship(back_populates="discovered_in_run")


class CompanyRecord(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("source_platform", "company_identifier", name="uq_company_source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_platform: Mapped[str] = mapped_column(String(30), index=True)
    company_name: Mapped[str] = mapped_column(String(200), index=True)
    company_identifier: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    last_scan_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_scan_warnings: Mapped[list] = mapped_column(JSON, default=list)


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "source_platform",
            "company_identifier",
            "external_job_id",
            name="uq_job_source_company_external",
        ),
        Index("ix_jobs_active_score", "active", "match_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_platform: Mapped[str] = mapped_column(String(30), index=True)
    company_name: Mapped[str] = mapped_column(String(200), index=True)
    company_identifier: Mapped[str] = mapped_column(String(100))
    external_job_id: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500), index=True)
    location: Mapped[str] = mapped_column(String(500), default="Unknown", index=True)
    remote_status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    employment_type: Mapped[str | None] = mapped_column(String(200))
    department: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    date_posted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    match_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    match_reasons: Mapped[list] = mapped_column(JSON, default=list)
    rejection_reasons: Mapped[list] = mapped_column(JSON, default=list)
    preference_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screening_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_reasons: Mapped[list] = mapped_column(JSON, default=list)
    resume_gaps: Mapped[list] = mapped_column(JSON, default=list)
    screening_reasons: Mapped[list] = mapped_column(JSON, default=list)
    screening_flags: Mapped[list] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    discovered_in_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("scan_runs.id"), nullable=True
    )

    discovered_in_run: Mapped[ScanRun | None] = relationship(back_populates="jobs")


def create_session_factory(database_path: Path) -> sessionmaker:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    _migrate_jobs_score_breakdown(engine)
    _migrate_scan_pruned_jobs(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def database_engine(factory: sessionmaker) -> Engine:
    return factory.kw["bind"]


def _migrate_jobs_score_breakdown(engine: Engine) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns("jobs")}
    additions = {
        "preference_score": "INTEGER",
        "resume_score": "INTEGER",
        "screening_score": "INTEGER",
        "resume_reasons": "JSON NOT NULL DEFAULT '[]'",
        "resume_gaps": "JSON NOT NULL DEFAULT '[]'",
        "screening_reasons": "JSON NOT NULL DEFAULT '[]'",
        "screening_flags": "JSON NOT NULL DEFAULT '[]'",
    }
    with engine.begin() as connection:
        for name, declaration in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}"))


def _migrate_scan_pruned_jobs(engine: Engine) -> None:
    existing = {column["name"] for column in inspect(engine).get_columns("scan_runs")}
    if "pruned_jobs" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE scan_runs ADD COLUMN pruned_jobs INTEGER NOT NULL DEFAULT 0")
            )
