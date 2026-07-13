from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_discovery.database import JobRecord


def _job_data(job: JobRecord, run_id: int) -> dict[str, Any]:
    return {
        "id": job.id,
        "is_new": job.discovered_in_run_id == run_id,
        "company": job.company_name,
        "title": job.title,
        "location": job.location,
        "remote_status": job.remote_status,
        "source": job.source_platform,
        "score": job.match_score,
        "preference_score": job.preference_score,
        "resume_score": job.resume_score,
        "screening_score": job.screening_score,
        "match_reasons": job.match_reasons,
        "rejection_reasons": job.rejection_reasons,
        "resume_reasons": job.resume_reasons,
        "resume_gaps": job.resume_gaps,
        "screening_reasons": job.screening_reasons,
        "screening_flags": job.screening_flags,
        "apply_url": job.apply_url,
        "source_url": job.source_url,
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def write_reports(
    reports_dir: Path,
    *,
    run_id: int,
    status: str,
    jobs: list[JobRecord],
    errors: list[dict[str, str]],
    score_threshold: int,
) -> tuple[Path, Path]:
    generated_at = datetime.now(UTC).isoformat()
    serialized = [_job_data(job, run_id) for job in jobs]
    payload = {
        "generated_at": generated_at,
        "scan_run_id": run_id,
        "status": status,
        "score_threshold": score_threshold,
        "new_job_count": sum(1 for job in serialized if job["is_new"]),
        "jobs": serialized,
        "errors": errors,
    }

    json_path = reports_dir / "latest.json"
    markdown_path = reports_dir / "latest.md"
    _atomic_write(json_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    lines = [
        "# Personal job discovery report",
        "",
        f"Generated: {generated_at}",
        f"Scan status: **{status}**",
        f"Alert threshold: **{score_threshold}**",
        "",
    ]
    new_jobs = [job for job in serialized if job["is_new"]]
    lines.extend(["## Newly discovered jobs", ""])
    lines.extend(_markdown_jobs(new_jobs) if new_jobs else ["No new jobs in this scan.", ""])
    high_scores = [job for job in serialized if job["score"] >= score_threshold]
    lines.extend(["## Active jobs above threshold", ""])
    lines.extend(_markdown_jobs(high_scores) if high_scores else ["No matching jobs.", ""])
    lines.extend(["## Source errors", ""])
    if errors:
        lines.extend(f"- **{error['company']}**: {error['message']}" for error in errors)
    else:
        lines.append("No source errors.")
    lines.append("")
    _atomic_write(markdown_path, "\n".join(lines))
    return markdown_path, json_path


def _markdown_jobs(jobs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for job in jobs:
        lines.extend(
            [
                f"### {job['company']} — {job['title']}",
                "",
                f"- Location: {job['location']}",
                f"- Score: {job['score']}",
                f"- Preference: {job['preference_score']}",
                f"- Resume evidence: {job['resume_score']}",
                f"- Screening readiness: {job['screening_score']}",
                f"- Source: {job['source']}",
                f"- Match reasons: {'; '.join(job['match_reasons']) or 'None'}",
                f"- Rejection reasons: {'; '.join(job['rejection_reasons']) or 'None'}",
                f"- Resume evidence: {'; '.join(job['resume_reasons']) or 'None'}",
                f"- Resume gaps: {'; '.join(job['resume_gaps']) or 'None'}",
                f"- Screening notes: {'; '.join(job['screening_reasons']) or 'None'}",
                f"- Screening flags: {'; '.join(job['screening_flags']) or 'None'}",
                f"- [Apply]({job['apply_url']})",
                "",
            ]
        )
    return lines
