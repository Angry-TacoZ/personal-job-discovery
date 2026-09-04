from pathlib import Path

from fastapi.testclient import TestClient

from job_discovery.discovery import DiscoveryResult
from job_discovery.schemas import (
    CompanyConfig,
    NormalizedJob,
    ScoreResult,
    SourcePlatform,
)
from job_discovery.web.app import create_app


def test_dashboard_escapes_description_and_updates_review_status(
    tmp_path: Path, monkeypatch
):
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

    async def fake_discovery(company_name, *_args, **_kwargs):
        if company_name == "Existing Example":
            return DiscoveryResult(
                platform=SourcePlatform.GREENHOUSE,
                identifier="example",
                job_count=1,
                careers_url="https://example.com/careers",
            )
        return DiscoveryResult(
            platform=SourcePlatform.LEVER,
            identifier="detected-example",
            job_count=12,
            careers_url="https://detected.example/careers",
        )

    monkeypatch.setattr(
        "job_discovery.web.app.discover_company_source", fake_discovery
    )

    with TestClient(app) as client:
        health = client.get("/health")
        dashboard = client.get("/?minimum_score=20&new_only=true")
        remote_with_blank_score = client.get(
            "/?company=&title=&minimum_score=&source=&location=Remote&remote_status="
        )
        sorted_dashboard = client.get("/?sort_by=resume&sort_direction=asc&location=Remote")
        invalid_sort = client.get("/?sort_by=title&sort_direction=sideways")
        malformed_score = client.get("/?minimum_score=not-a-number")
        out_of_range_score = client.get("/?minimum_score=101")
        detail = client.get(f"/jobs/{job.id}")
        control = client.get("/control")
        resume_page = client.get("/control/resume")
        faq_page = client.get("/faq")
        discovery = client.post(
            "/control/companies/discover",
            data={
                "company_name": "Detected Example",
                "careers_url": "https://detected.example/careers",
            },
        )
        existing_discovery = client.post(
            "/control/companies/discover",
            data={
                "company_name": "Existing Example",
                "careers_url": "https://example.com/careers",
            },
        )
        rejected_discovery_origin = client.post(
            "/control/companies/discover",
            data={
                "company_name": "Detected Example",
                "careers_url": "https://detected.example/careers",
            },
            headers={"Origin": "https://untrusted.example"},
        )
        update = client.post(
            f"/jobs/{job.id}/status?status=ignored", follow_redirects=False
        )
        rejected_origin = client.post(
            f"/jobs/{job.id}/status?status=reviewed",
            headers={"Origin": "https://untrusted.example"},
        )
        settings = client.post(
            "/control/settings",
            data={
                "threshold": "33",
                "prune_below_score": "40",
                "pruning_enabled": "true",
                "timeout": "15",
                "retries": "1",
            },
            follow_redirects=False,
        )
        enabled_config = app.state.config_store.app_config()
        settings_disabled = client.post(
            "/control/settings",
            data={
                "threshold": "33",
                "prune_below_score": "40",
                "timeout": "15",
                "retries": "1",
            },
            follow_redirects=False,
        )
        disabled_config = app.state.config_store.app_config()
        settings_reenabled = client.post(
            "/control/settings",
            data={
                "threshold": "33",
                "prune_below_score": "55",
                "pruning_enabled": "true",
                "timeout": "15",
                "retries": "1",
            },
            follow_redirects=False,
        )
        company_add = client.post(
            "/control/companies/save",
            data={
                "company_name": "Second Example",
                "ats_platform": "lever",
                "ats_identifier": "second-example",
                "notes": "Added in the GUI",
                "enabled": "on",
                "index": "",
            },
            follow_redirects=False,
        )

    assert health.json() == {"status": "ok", "jobs": 1}
    assert "Operations Analyst" in dashboard.text
    assert remote_with_blank_score.status_code == 200
    assert "Operations Analyst" in remote_with_blank_score.text
    assert sorted_dashboard.status_code == 200
    assert 'id="job-filters"' in sorted_dashboard.text
    assert sorted_dashboard.text.count('form="job-filters"') == 2
    assert sorted_dashboard.text.count("this.form.requestSubmit()") == 2
    assert "styles.css?v=20260713-company-discovery" in sorted_dashboard.text
    assert '<option value="resume" selected>Skills match</option>' in sorted_dashboard.text
    assert '<option value="asc" selected>Lowest first</option>' in sorted_dashboard.text
    assert invalid_sort.status_code == 422
    assert malformed_score.status_code == 422
    assert out_of_range_score.status_code == 422
    assert "<script>" not in detail.text
    assert "&lt;script&gt;" in detail.text
    assert control.status_code == 200
    assert "Job discovery at a glance" in control.text
    assert " EDT" in dashboard.text
    assert " EDT" in control.text
    assert " UTC" not in dashboard.text
    assert resume_page.status_code == 200
    assert "No local resume profile configured" in resume_page.text
    assert faq_page.status_code == 200
    assert "What the job scores mean" in faq_page.text
    assert "Preference fit" in faq_page.text
    assert "Skills match" in faq_page.text
    assert "Eligibility fit" in faq_page.text
    assert "private ATS" in faq_page.text
    assert discovery.status_code == 200
    assert "Ready to confirm" in discovery.text
    assert "Detected <strong>Lever</strong>" in discovery.text
    assert "12</strong> current jobs" in discovery.text
    assert 'value="detected-example"' in discovery.text
    assert "Already monitored" in existing_discovery.text
    assert 'href="/control/companies?edit=0"' in existing_discovery.text
    assert "Add and monitor" not in existing_discovery.text
    assert rejected_discovery_origin.status_code == 403
    assert update.status_code == 303
    assert rejected_origin.status_code == 403
    assert repository.get_job(job.id).review_status == "ignored"
    assert settings.status_code == 303
    assert enabled_config.score_alert_threshold == 33
    assert enabled_config.prune_below_score == 40
    assert settings_disabled.status_code == 303
    assert disabled_config.prune_below_score is None
    assert settings_reenabled.status_code == 303
    assert app.state.config_store.app_config().prune_below_score == 55
    assert company_add.status_code == 303
    assert [company.company_name for company in app.state.config_store.companies()] == [
        "Example",
        "Second Example",
    ]
