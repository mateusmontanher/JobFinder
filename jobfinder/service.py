"""Application service for the matching use case."""

from __future__ import annotations

from .domain import MatchResult, Resume
from .matching import ResumeMatcher
from .parsing import Job


class JobMatchingService:
    """Coordinates matching while depending only on the matcher abstraction."""

    def __init__(self, matcher: ResumeMatcher | None = None) -> None:
        self._matcher = matcher or ResumeMatcher()

    def match(self, resume: Resume, jobs: list[Job]) -> list[MatchResult]:
        return self._matcher.rank(resume, jobs)
