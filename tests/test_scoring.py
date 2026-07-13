from job_discovery.schemas import NormalizedJob, ScoreRule, ScoringConfig
from job_discovery.scoring import score_job


def make_job() -> NormalizedJob:
    return NormalizedJob(
        source_platform="lever",
        company_name="Example",
        company_identifier="example",
        external_job_id="1",
        title="AI Operations Analyst",
        location="Remote - United States",
        description="Python workflow automation without cold calling.",
        apply_url="https://example.com/apply",
        source_url="https://example.com/job",
    )


def test_score_has_positive_and_negative_explanations():
    config = ScoringConfig(
        positive_rules=[
            ScoreRule(reason="Preferred role", phrases=["ai operations"], weight=20),
            ScoreRule(reason="Useful tool", phrases=["python"], weight=5),
        ],
        negative_rules=[
            ScoreRule(reason="Phone mismatch", phrases=["cold calling"], weight=-30)
        ],
    )

    result = score_job(make_job(), config)

    assert result.score == -5
    assert result.match_reasons == [
        "Preferred role (+20: ai operations)",
        "Useful tool (+5: python)",
    ]
    assert result.rejection_reasons == ["Phone mismatch (-30: cold calling)"]


def test_score_is_clamped_to_configured_range():
    config = ScoringConfig(
        minimum_score=-10,
        maximum_score=10,
        positive_rules=[ScoreRule(reason="Remote", phrases=["remote"], weight=100)],
    )
    assert score_job(make_job(), config).score == 10

