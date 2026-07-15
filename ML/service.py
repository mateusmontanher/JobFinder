"""Application layer for extracting profiles and ranking jobs."""

from __future__ import annotations

from collections.abc import Iterable

from .domain import CompatibilityResult, JobProfile
from .extraction import SpacyProfileExtractor
from .matching import CompatibilityScorer


class MatchingService:
    def __init__(self, extractor: SpacyProfileExtractor | None = None, scorer: CompatibilityScorer | None = None) -> None:
        self.extractor = extractor or SpacyProfileExtractor()
        self.scorer = scorer or CompatibilityScorer()

    def compare(self, resume_text: str, job_text: str, *, title: str = "") -> CompatibilityResult:
        candidate = self.extractor.candidate(resume_text)
        job = self.extractor.job(job_text, title=title)
        return self.scorer.score(candidate, job)

    def rank(self, resume_text: str, jobs: Iterable[JobProfile]) -> list[tuple[JobProfile, CompatibilityResult]]:
        candidate = self.extractor.candidate(resume_text)
        results = [(job, self.scorer.score(candidate, job)) for job in jobs]
        return sorted((item for item in results if item[1].selected), key=lambda item: -item[1].score)
