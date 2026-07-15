"""Domain data structures with no dependency on I/O or NLP libraries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Resume:
    """The text used to represent a candidate's experience."""

    text: str


@dataclass(frozen=True)
class Job:
    """A normalized job record while retaining the original input fields."""

    identifier: str
    title: str
    description: str
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}".strip()


@dataclass(frozen=True)
class MatchResult:
    """A scored job suitable for a JSONL result file."""

    job: Job
    score: float
    reasons: tuple[str, ...]

    def to_record(self) -> dict[str, Any]:
        record = dict(self.job.raw)
        record.update(
            {
                "id": self.job.identifier,
                "title": self.job.title,
                "description": self.job.description,
                "compatibility_score": round(self.score, 4),
                "match_reasons": list(self.reasons),
            }
        )
        return record
