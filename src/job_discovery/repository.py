from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import Select, asc, delete, desc, func, select
from sqlalchemy.orm import Session, sessionmaker

from job_discovery.database import AppState, CompanyRecord, JobRecord, ScanRun
from job_discovery.schemas import (
    CompanyConfig,
    NormalizedJob,
    ResumeProfile,
    ScoreResult,
    ScoringConfig,
)
from job_discovery.scoring import SCORING_VERSION, score_job


def utc_now() -> datetime:
    return datetime.now(UTC)


class Repository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def sync_companies(self, companies: list[CompanyConfig]) -> None:
        with self.session_factory.begin() as session:
            for company in companies:
                record = self._company(session, company)
                if record is None:
                    session.add(
                        CompanyRecord(
                            source_platform=company.ats_platform.value,
                            company_name=company.company_name,
                            company_identifier=company.ats_identifier,
                            enabled=company.enabled,
                            notes=company.notes,
                        )
                    )
                else:
                    record.company_name = company.company_name
                    record.enabled = company.enabled
                    record.notes = company.notes

    def start_scan(self, companies_attempted: int) -> int:
        with self.session_factory.begin() as session:
            run = ScanRun(started_at=utc_now(), companies_attempted=companies_attempted)
            session.add(run)
            session.flush()
            return run.id

    def record_source_failure(self, company: CompanyConfig, message: str) -> None:
        with self.session_factory.begin() as session:
            record = self._company(session, company)
            if record is None:
                raise RuntimeError("company must be synchronized before scanning")
            record.last_scan_attempt_at = utc_now()
            record.last_scan_error = message[:4000]

    def upsert_source_jobs(
        self,
        company: CompanyConfig,
        run_id: int,
        jobs_with_scores: list[tuple[NormalizedJob, ScoreResult]],
        warnings: list[str],
        prune_below_score: int | None = None,
    ) -> tuple[int, int, int, int]:
        now = utc_now()
        new_count = 0
        updated_count = 0
        inactive_count = 0
        pruned_count = 0
        seen_ids = {job.external_job_id for job, _score in jobs_with_scores}

        with self.session_factory.begin() as session:
            company_record = self._company(session, company)
            if company_record is None:
                raise RuntimeError("company must be synchronized before scanning")
            company_record.last_scan_attempt_at = now
            company_record.last_scan_success_at = now
            company_record.last_scan_error = None
            company_record.last_scan_warnings = warnings[:100]

            for job, score in jobs_with_scores:
                record = session.scalar(
                    select(JobRecord).where(
                        JobRecord.source_platform == job.source_platform.value,
                        JobRecord.company_identifier == job.company_identifier,
                        JobRecord.external_job_id == job.external_job_id,
                    )
                )
                content_hash = job.content_hash()
                if prune_below_score is not None and score.score < prune_below_score:
                    if record is not None:
                        session.delete(record)
                    pruned_count += 1
                    continue
                if record is None:
                    record = JobRecord(
                        first_seen_at=now,
                        discovered_in_run_id=run_id,
                        review_status="new",
                        source_platform=job.source_platform.value,
                        company_identifier=job.company_identifier,
                        external_job_id=job.external_job_id,
                        title=job.title,
                        company_name=job.company_name,
                        location=job.location,
                        remote_status=job.remote_status.value,
                        apply_url=str(job.apply_url),
                        source_url=str(job.source_url),
                        last_seen_at=now,
                        active=True,
                        content_hash=content_hash,
                    )
                    session.add(record)
                    new_count += 1
                else:
                    updated_count += 1

                record.company_name = job.company_name
                record.title = job.title
                record.location = job.location
                record.remote_status = job.remote_status.value
                record.employment_type = job.employment_type
                record.department = job.department
                record.description = job.description
                record.apply_url = str(job.apply_url)
                record.source_url = str(job.source_url)
                record.date_posted = job.date_posted
                record.last_seen_at = now
                record.active = True
                record.content_hash = content_hash
                self._apply_score(record, score)

            if prune_below_score is not None:
                session.flush()
                stale_low_scores = select(JobRecord).where(
                    JobRecord.source_platform == company.ats_platform.value,
                    JobRecord.company_identifier == company.ats_identifier,
                    JobRecord.match_score < prune_below_score,
                )
                for low_score in session.scalars(stale_low_scores):
                    session.delete(low_score)
                    pruned_count += 1

            session.flush()

            active_query = select(JobRecord).where(
                JobRecord.source_platform == company.ats_platform.value,
                JobRecord.company_identifier == company.ats_identifier,
                JobRecord.active.is_(True),
            )
            if seen_ids:
                active_query = active_query.where(JobRecord.external_job_id.not_in(seen_ids))
            for missing in session.scalars(active_query):
                missing.active = False
                inactive_count += 1

        return new_count, updated_count, inactive_count, pruned_count

    def finish_scan(
        self,
        run_id: int,
        *,
        succeeded: int,
        new_jobs: int,
        updated_jobs: int,
        inactive_jobs: int,
        pruned_jobs: int,
        errors: list[dict[str, str]],
    ) -> None:
        with self.session_factory.begin() as session:
            run = session.get(ScanRun, run_id)
            if run is None:
                raise RuntimeError(f"scan run {run_id} not found")
            run.finished_at = utc_now()
            run.companies_succeeded = succeeded
            run.new_jobs = new_jobs
            run.updated_jobs = updated_jobs
            run.inactive_jobs = inactive_jobs
            run.pruned_jobs = pruned_jobs
            run.error_count = len(errors)
            run.status = "success" if not errors else ("partial" if succeeded else "failed")
            run.summary = {"errors": errors, "pruned_jobs": pruned_jobs}

    def jobs_for_report(self, run_id: int, score_threshold: int) -> list[JobRecord]:
        with self.session_factory() as session:
            query = (
                select(JobRecord)
                .where(
                    JobRecord.active.is_(True),
                    (JobRecord.discovered_in_run_id == run_id)
                    | (JobRecord.match_score >= score_threshold),
                )
                .order_by(desc(JobRecord.match_score), desc(JobRecord.first_seen_at))
            )
            return list(session.scalars(query))

    def get_job(self, job_id: int) -> JobRecord | None:
        with self.session_factory() as session:
            return session.get(JobRecord, job_id)

    def set_review_status(self, job_id: int, status: str) -> bool:
        if status not in {"new", "reviewed", "ignored"}:
            raise ValueError("invalid review status")
        with self.session_factory.begin() as session:
            record = session.get(JobRecord, job_id)
            if record is None:
                return False
            record.review_status = status
            return True

    def dashboard_jobs(
        self,
        *,
        company: str | None = None,
        title: str | None = None,
        source: str | None = None,
        location: str | None = None,
        remote_status: str | None = None,
        minimum_score: int | None = None,
        new_only: bool = False,
        sort_by: str = "overall",
        sort_direction: str = "desc",
        limit: int = 250,
    ) -> list[JobRecord]:
        sort_columns = {
            "overall": JobRecord.match_score,
            "preference": JobRecord.preference_score,
            "resume": JobRecord.resume_score,
            "screen": JobRecord.screening_score,
        }
        if sort_by not in sort_columns:
            raise ValueError("invalid dashboard sort field")
        if sort_direction not in {"asc", "desc"}:
            raise ValueError("invalid dashboard sort direction")

        query: Select[tuple[JobRecord]] = select(JobRecord).where(JobRecord.active.is_(True))
        if company:
            query = query.where(JobRecord.company_name == company)
        if title:
            query = query.where(JobRecord.title.ilike(f"%{title}%"))
        if source:
            query = query.where(JobRecord.source_platform == source)
        if location:
            query = query.where(JobRecord.location.ilike(f"%{location}%"))
        if remote_status:
            query = query.where(JobRecord.remote_status == remote_status)
        if minimum_score is not None:
            query = query.where(JobRecord.match_score >= minimum_score)
        if new_only:
            query = query.where(JobRecord.review_status == "new")
        sort_column = sort_columns[sort_by]
        direction = asc if sort_direction == "asc" else desc
        query = query.order_by(
            sort_column.is_(None),
            direction(sort_column),
            desc(JobRecord.match_score),
            desc(JobRecord.first_seen_at),
        ).limit(min(limit, 500))
        with self.session_factory() as session:
            return list(session.scalars(query))

    def companies(self) -> list[CompanyRecord]:
        with self.session_factory() as session:
            return list(session.scalars(select(CompanyRecord).order_by(CompanyRecord.company_name)))

    def latest_scan(self) -> ScanRun | None:
        with self.session_factory() as session:
            return session.scalar(select(ScanRun).order_by(desc(ScanRun.started_at)).limit(1))

    def filter_options(self) -> dict[str, list[str]]:
        with self.session_factory() as session:
            def values(column: object) -> list[str]:
                return [
                    value
                    for value in session.scalars(
                        select(column).where(column.is_not(None)).distinct().order_by(column)
                    )
                    if value
                ]

            return {
                "companies": values(JobRecord.company_name),
                "sources": values(JobRecord.source_platform),
                "remote_statuses": values(JobRecord.remote_status),
            }

    def count_jobs(self) -> int:
        with self.session_factory() as session:
            return session.scalar(select(func.count()).select_from(JobRecord)) or 0

    def rescore_jobs(
        self, scoring: ScoringConfig, resume_profile: ResumeProfile | None
    ) -> int:
        count = 0
        with self.session_factory.begin() as session:
            for record in session.scalars(select(JobRecord).where(JobRecord.active.is_(True))):
                self._apply_score(record, score_job(record, scoring, resume_profile))
                count += 1
            signature = self._scoring_signature(scoring, resume_profile)
            state = session.get(AppState, "scoring_signature")
            if state is None:
                session.add(AppState(key="scoring_signature", value=signature))
            else:
                state.value = signature
        return count

    def prune_jobs_below(self, threshold: int) -> int:
        with self.session_factory.begin() as session:
            result = session.execute(delete(JobRecord).where(JobRecord.match_score < threshold))
            return result.rowcount or 0

    def ensure_scores(
        self, scoring: ScoringConfig, resume_profile: ResumeProfile | None
    ) -> int:
        signature = self._scoring_signature(scoring, resume_profile)
        with self.session_factory() as session:
            state = session.get(AppState, "scoring_signature")
            if state is not None and state.value == signature:
                return 0
        return self.rescore_jobs(scoring, resume_profile)

    def summary_counts(self, score_threshold: int) -> dict[str, int]:
        with self.session_factory() as session:
            active = JobRecord.active.is_(True)

            def count(*conditions: object) -> int:
                query = select(func.count()).select_from(JobRecord).where(active, *conditions)
                return session.scalar(query) or 0

            return {
                "active": count(),
                "new": count(JobRecord.review_status == "new"),
                "strong": count(JobRecord.match_score >= score_threshold),
                "ignored": count(JobRecord.review_status == "ignored"),
            }

    @staticmethod
    def _company(session: Session, company: CompanyConfig) -> CompanyRecord | None:
        return session.scalar(
            select(CompanyRecord).where(
                CompanyRecord.source_platform == company.ats_platform.value,
                CompanyRecord.company_identifier == company.ats_identifier,
            )
        )

    @staticmethod
    def _apply_score(record: JobRecord, score: ScoreResult) -> None:
        record.match_score = score.score
        record.preference_score = score.preference_score
        record.resume_score = score.resume_score
        record.screening_score = score.screening_score
        record.match_reasons = score.match_reasons
        record.rejection_reasons = score.rejection_reasons
        record.resume_reasons = score.resume_reasons
        record.resume_gaps = score.resume_gaps
        record.screening_reasons = score.screening_reasons
        record.screening_flags = score.screening_flags

    @staticmethod
    def _scoring_signature(
        scoring: ScoringConfig, resume_profile: ResumeProfile | None
    ) -> str:
        payload = {
            "version": SCORING_VERSION,
            "scoring": scoring.model_dump(mode="json"),
            "resume_profile": (
                resume_profile.model_dump(mode="json") if resume_profile else None
            ),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
