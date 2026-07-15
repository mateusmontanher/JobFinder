"""Configurable, explainable compatibility scoring."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import CandidateProfile, CompatibilityResult, JobProfile, ScoreWeights
from .extraction import normalize_text

SENIORITY_RANK = {"intern": 0, "junior": 1, "mid": 2, "senior": 3, "lead": 4}


@dataclass(frozen=True)
class MatchConfig:
    threshold: float = 0.60
    weights: ScoreWeights = ScoreWeights()

    def __post_init__(self) -> None:
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")


class CompatibilityScorer:
    def __init__(self, config: MatchConfig | None = None) -> None:
        self.config = config or MatchConfig()

    def score(self, candidate: CandidateProfile, job: JobProfile) -> CompatibilityResult:
        candidate_skills = candidate.skills | candidate.tools
        required_skills = job.skills | job.tools
        matched = candidate_skills & required_skills
        missing = required_skills - candidate_skills
        skills = len(matched) / len(required_skills) if required_skills else 0.5
        experience = self._experience(candidate.years_experience, job.years_experience)
        seniority = self._seniority(candidate.seniority, job.seniority)
        other = self._other(candidate, job)
        components = {"skills": skills, "experience": experience, "seniority": seniority, "other": other}
        weights = self.config.weights
        total = (
            skills * weights.skills + experience * weights.experience
            + seniority * weights.seniority + other * weights.other
        )
        reasons = [f"matched {len(matched)} of {len(required_skills)} detected skills"]
        if job.years_experience is not None:
            reasons.append(f"job requests {job.years_experience:g} years of experience")
        if candidate.seniority and job.seniority:
            reasons.append(f"seniority: candidate {candidate.seniority}, job {job.seniority}")
        return CompatibilityResult(
            score=max(0.0, min(1.0, total)), selected=total >= self.config.threshold,
            components=components, matched_skills=tuple(sorted(matched)),
            missing_skills=tuple(sorted(missing)), reasons=tuple(reasons),
        )

    @staticmethod
    def _experience(candidate: float | None, required: float | None) -> float:
        if required is None:
            return 0.60
        if candidate is None:
            return 0.35
        return min(1.0, candidate / max(required, 1.0))

    @staticmethod
    def _seniority(candidate: str | None, required: str | None) -> float:
        if required is None:
            return 0.70
        if candidate is None:
            return 0.5
        distance = abs(SENIORITY_RANK.get(candidate, 2) - SENIORITY_RANK.get(required, 2))
        return max(0.0, 1.0 - 0.35 * distance)

    @staticmethod
    def _other(candidate: CandidateProfile, job: JobProfile) -> float:
        education = 1.0 if not job.education else len(candidate.education & job.education) / len(job.education)
        candidate_terms = set(normalize_text(candidate.text).split())
        job_terms = set(normalize_text(job.text).split())
        context = len(candidate_terms & job_terms) / max(1, len(job_terms))
        return 0.6 * education + 0.4 * min(1.0, context * 4)
