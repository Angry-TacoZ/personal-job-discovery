from __future__ import annotations

from job_discovery.schemas import NormalizedJob, ScoreResult, ScoreRule, ScoringConfig

_ALLOWED_FIELDS = {"title", "description", "location", "department", "employment_type"}


def _matched(rule: ScoreRule, job: NormalizedJob) -> list[str]:
    haystacks: list[str] = []
    for field in rule.fields:
        if field not in _ALLOWED_FIELDS:
            continue
        value = getattr(job, field, None)
        if value:
            haystacks.append(str(value).casefold())
    return [phrase for phrase in rule.phrases if any(phrase in value for value in haystacks)]


def score_job(job: NormalizedJob, config: ScoringConfig) -> ScoreResult:
    total = 0
    matches: list[str] = []
    rejections: list[str] = []

    for rule in config.positive_rules:
        phrases = _matched(rule, job)
        if phrases:
            total += rule.weight
            matches.append(f"{rule.reason} ({rule.weight:+d}: {', '.join(phrases)})")

    for rule in config.negative_rules:
        phrases = _matched(rule, job)
        if phrases:
            total += rule.weight
            rejections.append(f"{rule.reason} ({rule.weight:+d}: {', '.join(phrases)})")

    score = max(config.minimum_score, min(config.maximum_score, total))
    return ScoreResult(score=score, match_reasons=matches, rejection_reasons=rejections)

