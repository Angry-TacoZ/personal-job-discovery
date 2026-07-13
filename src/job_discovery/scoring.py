from __future__ import annotations

import re
from typing import Protocol

from job_discovery.schemas import ResumeProfile, ScoreResult, ScoreRule, ScoringConfig

_ALLOWED_FIELDS = {"title", "description", "location", "department", "employment_type"}
_YEAR_REQUIREMENT = re.compile(r"(?<!\d)(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE)
_BACHELOR = re.compile(r"bachelor(?:'s|s)?(?: degree)?", re.IGNORECASE)
_ADVANCED_DEGREE = re.compile(
    r"(?:master(?:'s|s)?(?: degree)?|ph\.?d\.?|doctorate|advanced degree)", re.IGNORECASE
)
_REQUIREMENT_WORDS = re.compile(r"required|must have|minimum qualification", re.IGNORECASE)
SCORING_VERSION = 1


class ScorableJob(Protocol):
    title: str
    description: str
    location: str
    department: str | None
    employment_type: str | None
    remote_status: object


def _contains_phrase(value: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", value))


def _matched(rule: ScoreRule, job: ScorableJob) -> list[str]:
    haystacks: list[str] = []
    for field in rule.fields:
        if field not in _ALLOWED_FIELDS:
            continue
        value = getattr(job, field, None)
        if value:
            haystacks.append(str(value).casefold())
    return [
        phrase
        for phrase in rule.phrases
        if any(_contains_phrase(value, phrase) for value in haystacks)
    ]


def _required_near(text: str, pattern: re.Pattern[str]) -> bool:
    for match in pattern.finditer(text):
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        if _REQUIREMENT_WORDS.search(text[start:end]):
            return True
    return False


def _screening_score(job: ScorableJob, profile: ResumeProfile) -> tuple[int, list[str], list[str]]:
    title = job.title.casefold()
    description = job.description.casefold()
    location = job.location.casefold()
    combined = f"{title}\n{description}"
    score = 70
    reasons: list[str] = []
    flags: list[str] = []

    listed_location = f"{title} {location}"
    excluded_location = next(
        (
            place
            for place in profile.excluded_locations
            if _contains_phrase(listed_location, place)
        ),
        None,
    )
    if excluded_location:
        score -= 25
        flags.append(
            f"Likely blocker: location appears restricted to {excluded_location.title()}; "
            "resume location is Pennsylvania."
        )
    elif any(_contains_phrase(listed_location, place) for place in profile.locations):
        score += 10
        reasons.append("Location appears compatible with the resume profile.")

    years = [int(value) for value in _YEAR_REQUIREMENT.findall(description)]
    if years:
        maximum = max(years)
        if maximum <= profile.years_experience:
            score += 10
            reasons.append(
                f"Posting mentions up to {maximum} years; resume documents "
                f"{profile.years_experience} years of total experience."
            )
        else:
            score -= 25
            flags.append(
                f"Likely blocker: posting mentions {maximum} years; resume documents "
                f"{profile.years_experience} years total. Verify relevance before applying."
            )

    equivalent_experience = any(
        phrase in description
        for phrase in ("or equivalent experience", "or equivalent practical experience")
    )
    if _required_near(combined, _ADVANCED_DEGREE) and profile.education_level not in {
        "master",
        "doctorate",
    }:
        score -= 35
        flags.append("Likely blocker: posting explicitly requires an advanced degree.")
    elif _required_near(combined, _BACHELOR) and profile.education_level in {
        "high_school",
        "associate",
    }:
        if equivalent_experience:
            score -= 5
            flags.append(
                "Verify: posting allows equivalent experience, but the employer "
                "decides equivalency."
            )
        else:
            score -= 30
            flags.append("Likely blocker: posting explicitly requires a bachelor's degree.")
    elif _BACHELOR.search(description) and profile.education_level in {
        "high_school",
        "associate",
    }:
        score -= 5
        flags.append(
            "Verify: posting mentions a bachelor's degree, but no explicit "
            "requirement was found."
        )

    active_clearance_required = any(
        phrase in combined
        for phrase in (
            "active security clearance",
            "active secret clearance",
            "active top secret",
            "active ts/sci",
        )
    )
    if active_clearance_required and not profile.active_clearance:
        score -= 35
        flags.append(
            "Likely blocker: posting requires an active clearance not stated in the resume."
        )
    elif any(
        phrase in combined
        for phrase in ("ability to obtain a clearance", "clearance eligible")
    ):
        score -= 8
        flags.append("Verify: posting may require eligibility to obtain a security clearance.")

    if profile.work_authorization == "unknown" and any(
        phrase in description
        for phrase in (
            "no sponsorship",
            "unable to sponsor",
            "must be authorized to work",
            "without sponsorship",
        )
    ):
        score -= 5
        flags.append(
            "Unknown: posting restricts sponsorship; resume does not state work authorization."
        )

    if any(level in title for level in ("senior", "staff", "principal")):
        score -= 10
        flags.append("Competitive stretch: title signals senior-level scope.")

    if not reasons:
        reasons.append("No explicit location or experience requirement could be confirmed.")
    return max(0, min(100, score)), reasons, flags


def score_job(
    job: ScorableJob,
    config: ScoringConfig,
    resume_profile: ResumeProfile | None = None,
) -> ScoreResult:
    preference_total = 0
    matches: list[str] = []
    rejections: list[str] = []

    for rule in config.positive_rules:
        phrases = _matched(rule, job)
        if phrases:
            preference_total += rule.weight
            matches.append(f"{rule.reason} ({rule.weight:+d}: {', '.join(phrases)})")

    for rule in config.negative_rules:
        phrases = _matched(rule, job)
        if phrases:
            preference_total += rule.weight
            rejections.append(f"{rule.reason} ({rule.weight:+d}: {', '.join(phrases)})")

    raw_preference = max(config.minimum_score, min(config.maximum_score, preference_total))
    if resume_profile is None:
        return ScoreResult(
            score=raw_preference,
            preference_score=raw_preference,
            match_reasons=matches,
            rejection_reasons=rejections,
        )

    preference_score = max(0, min(100, 50 + raw_preference))
    resume_total = 0
    resume_reasons: list[str] = []
    resume_gaps: list[str] = []
    for rule in resume_profile.evidence_rules:
        phrases = _matched(rule, job)
        if phrases:
            resume_total += rule.weight
            resume_reasons.append(
                f"{rule.reason} ({rule.weight:+d}: {', '.join(phrases)})"
            )
    for rule in resume_profile.gap_rules:
        phrases = _matched(rule, job)
        if phrases:
            resume_total += rule.weight
            resume_gaps.append(
                f"{rule.reason} ({rule.weight:+d}: {', '.join(phrases)})"
            )
    resume_score = max(0, min(100, resume_total))
    screening_score, screening_reasons, screening_flags = _screening_score(
        job, resume_profile
    )
    weighted = preference_score * 0.25 + resume_score * 0.50 + screening_score * 0.25
    overall = int(weighted + 0.5)
    if any(flag.startswith("Likely blocker:") for flag in screening_flags):
        overall = min(overall, 49)

    return ScoreResult(
        score=overall,
        preference_score=preference_score,
        resume_score=resume_score,
        screening_score=screening_score,
        match_reasons=matches,
        rejection_reasons=rejections,
        resume_reasons=resume_reasons,
        resume_gaps=resume_gaps,
        screening_reasons=screening_reasons,
        screening_flags=screening_flags,
    )
