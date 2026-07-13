from job_discovery.schemas import NormalizedJob, ResumeProfile, ScoreRule, ScoringConfig
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


def make_profile() -> ResumeProfile:
    return ResumeProfile(
        profile_label="Sanitized test profile",
        years_experience=8,
        education_level="high_school",
        locations=["remote", "pennsylvania"],
        evidence_rules=[
            ScoreRule(
                reason="Documented Python delivery",
                phrases=["python"],
                weight=60,
                fields=["title", "description"],
            )
        ],
    )


def test_resume_aware_score_exposes_all_three_components():
    config = ScoringConfig(
        positive_rules=[ScoreRule(reason="Preferred role", phrases=["ai operations"], weight=20)],
        negative_rules=[ScoreRule(reason="Phone mismatch", phrases=["cold calling"], weight=-30)],
    )
    job = make_job().model_copy(
        update={
            "description": (
                "Python workflow automation without cold calling. Requires 5 years of experience."
            )
        }
    )

    result = score_job(job, config, make_profile())

    assert result.preference_score == 40
    assert result.resume_score == 60
    assert result.screening_score == 90
    assert result.score == 63
    assert result.resume_reasons == ["Documented Python delivery (+60: python)"]
    assert any("5 years" in reason for reason in result.screening_reasons)


def test_explicit_requirements_create_screening_blockers_and_cap_overall_score():
    job = make_job().model_copy(
        update={
            "title": "Senior Systems Engineer",
            "description": (
                "10+ years required. Bachelor's degree required. "
                "Active security clearance required. Python."
            ),
        }
    )

    result = score_job(job, ScoringConfig(), make_profile())

    assert result.screening_score == 0
    assert result.score <= 49
    assert sum(flag.startswith("Likely blocker:") for flag in result.screening_flags) == 3


def test_short_skill_names_do_not_match_inside_unrelated_words():
    profile = make_profile().model_copy(
        update={
            "evidence_rules": [
                ScoreRule(
                    reason="Short technical terms",
                    phrases=["rag", "git", "phi"],
                    weight=80,
                )
            ]
        }
    )
    job = make_job().model_copy(
        update={"description": "Average digital philosophy program management."}
    )

    result = score_job(job, ScoringConfig(), profile)

    assert result.resume_score == 0
    assert result.resume_reasons == []


def test_unstated_required_stack_reduces_resume_evidence_score():
    profile = make_profile().model_copy(
        update={
            "gap_rules": [
                ScoreRule(
                    reason="Primary stack not stated",
                    phrases=["ruby"],
                    weight=-28,
                    fields=["title", "description"],
                )
            ]
        }
    )
    job = make_job().model_copy(
        update={"title": "Ruby Engineer", "description": "Python and Ruby are required."}
    )

    result = score_job(job, ScoringConfig(), profile)

    assert result.resume_score == 32
    assert result.resume_gaps == ["Primary stack not stated (-28: ruby)"]


def test_explicit_foreign_location_is_a_screening_blocker_for_local_profile():
    profile = make_profile().model_copy(update={"excluded_locations": ["singapore"]})
    job = make_job().model_copy(update={"location": "Remote - Singapore"})

    result = score_job(job, ScoringConfig(), profile)

    assert result.screening_score == 45
    assert result.score <= 49
    assert result.screening_flags == [
        "Likely blocker: location appears restricted to Singapore; "
        "resume location is Pennsylvania."
    ]
