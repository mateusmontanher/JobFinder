"""Domain models for local resume-to-job matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class CandidateProfile:
    text: str
    skills: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()
    education: frozenset[str] = frozenset()
    years_experience: float | None = None
    seniority: str | None = None


@dataclass(frozen=True)
class JobProfile(CandidateProfile):
    identifier: str = ""
    title: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreWeights:
    skills: float = 0.40
    experience: float = 0.30
    seniority: float = 0.20
    other: float = 0.10

    def __post_init__(self) -> None:
        values = (self.skills, self.experience, self.seniority, self.other)
        if any(value < 0 for value in values) or abs(sum(values) - 1.0) > 1e-9:
            raise ValueError("score weights must be non-negative and sum to 1.0")


@dataclass(frozen=True)
class CompatibilityResult:
    score: float
    selected: bool
    components: Mapping[str, float]
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "compatibility_score": round(self.score * 100, 2),
            "selected": self.selected,
            "components": {name: round(value * 100, 2) for name, value in self.components.items()},
            "matched_skills": list(self.matched_skills),
            "missing_skills": list(self.missing_skills),
            "reasons": list(self.reasons),
        }
