from pathlib import Path

from fastapi.testclient import TestClient

from job_discovery.schemas import CompanyConfig, NormalizedJob, ScoreResult
from job_discovery.web.app import create_app


def test_dashboard_escapes_description_and_updates_review_status(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "companies.yml"
    config_path.write_text(
        """
database_path: data/jobs.db
reports_dir: reports
companies:
  - company_name: Example
    ats_platform: greenhouse
    ats_identifier: example
scoring: {}
""".strip(),
        encoding="utf-8",
    )
    app = create_app(config_path)
    repository = app.state.repository
    company = CompanyConfig(
        company_name="Example",
        ats_platform="greenhouse",
        ats_identifier="example",
    )
    run_id = repository.start_scan(1)
    repository.upsert_source_jobs(
        company,
        run_id,
        [
            (
                NormalizedJob(
                    source_platform="greenhouse",
                    company_name="Example",
                    company_identifier="example",
                    external_job_id="safe-1",
                    title="Operations Analyst",
                    location="Remote",
                    description="<script>alert('unsafe')</script>",
                    apply_url="https://example.com/apply",
                    source_url="https://example.com/job",
                ),
                ScoreResult(score=25, match_reasons=["Good fit"], rejection_reasons=[]),
            )
        ],
        [],
    )
    job = repository.dashboard_jobs()[0]

    with TestClient(app) as client:
        health = client.get("/health")
        dashboard = client.get("/?minimum_score=20&new_only=true")
        detail = client.get(f"/jobs/{job.id}")
        update = client.post(
            f"/jobs/{job.id}/status?status=ignored", follow_redirects=False
        )

    assert health.json() == {"status": "ok", "jobs": 1}
    assert "Operations Analyst" in dashboard.text
    assert "<script>" not in detail.text
    assert "&lt;script&gt;" in detail.text
    assert update.status_code == 303
    assert repository.get_job(job.id).review_status == "ignored"
