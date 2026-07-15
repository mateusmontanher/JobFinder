"""Feedback domain values without database, UI, or NLP dependencies."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

Rating = Literal["great", "bad"]
VALID_RATINGS: frozenset[str] = frozenset(("great", "bad"))
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z")


def stable_job_identifier(
    identifier: object,
    *,
    title: str = "",
    description: str = "",
    location: str = "",
) -> str:
    """Return a URL-safe source ID, or a deterministic hash for legacy rows."""
    candidate = str(identifier or "").strip()
    if SAFE_IDENTIFIER.fullmatch(candidate):
        return candidate
    payload = "\x1f".join((title.strip(), description.strip(), location.strip()))
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"legacy:{digest}"


@dataclass(frozen=True, slots=True)
class RatedJob:
    identifier: str
    title: str
    description: str
    location: str

    def __post_init__(self) -> None:
        if not SAFE_IDENTIFIER.fullmatch(self.identifier):
            raise ValueError("job identifier must be 1-128 URL-safe characters")


@dataclass(frozen=True, slots=True)
class FeedbackSnapshot:
    great: tuple[RatedJob, ...] = ()
    bad: tuple[RatedJob, ...] = ()

    @property
    def count(self) -> int:
        return len(self.great) + len(self.bad)

    def rating_for(self, identifier: str) -> Rating | None:
        if any(job.identifier == identifier for job in self.great):
            return "great"
        if any(job.identifier == identifier for job in self.bad):
            return "bad"
        return None

    def status_map(self) -> dict[str, Rating]:
        statuses: dict[str, Rating] = {job.identifier: "great" for job in self.great}
        statuses.update({job.identifier: "bad" for job in self.bad})
        return statuses
