"""Application orchestration for collection, matching, and persistence."""

from __future__ import annotations

import logging
from dataclasses import replace

from ML.extraction import SpacyProfileExtractor
from ML.feedback import FeedbackSimilarityScorer
from ML.matching import CompatibilityScorer, MatchConfig
from ML.repositories import PostgresResumeRepository
from jobfinder.feedback import (
    FeedbackSnapshot,
    RatedJob,
    SQLiteRatingRepository,
    stable_job_identifier,
)

from .collector import LinkedInCollector
from .domain import ScrapedJob, SearchSettings
from .repositories import PostgresJobRepository
from .search import build_search_url

LOGGER = logging.getLogger(__name__)


class JobSearchService:
    def __init__(
        self,
        *,
        resume_repository=None,
        job_repository=None,
        collector=None,
        extractor=None,
        rating_repository=None,
        feedback_scorer=None,
        settings: SearchSettings | None = None,
    ) -> None:
        self.resume_repository = resume_repository or PostgresResumeRepository()
        self.job_repository = job_repository or PostgresJobRepository()
        self.collector = collector or LinkedInCollector()
        self.extractor = extractor or SpacyProfileExtractor()
        self.rating_repository = rating_repository or SQLiteRatingRepository()
        self.feedback_scorer = feedback_scorer
        self.settings = settings or SearchSettings()
        self.scorer = CompatibilityScorer(MatchConfig(threshold=self.settings.minimum_similarity))

    def run(self) -> list[ScrapedJob]:
        feedback = self._load_feedback()
        resume_text = self.resume_repository.load_text()
        candidate = self.extractor.candidate(resume_text)
        keywords = self.extractor.keywords(resume_text)
        LOGGER.info("Starting local-profile job search with %d keywords", len(keywords))
        collected: list[ScrapedJob] = []
        limits = tuple(dict.fromkeys((3, 2, 1)))
        for term_limit in limits:
            url = build_search_url(keywords, self.settings, term_limit=term_limit)
            LOGGER.info("Searching LinkedIn with the top %d resume term(s)", term_limit)
            collected = self.collector.collect(url)
            if collected:
                break
            LOGGER.info("No cards found; broadening the resume-derived search")
        if not collected:
            LOGGER.error("LinkedIn returned no cards for any resume-derived query; existing jobs were preserved")
            return []
        collected = self._filter_bad_feedback(collected, feedback)
        selected: list[ScrapedJob] = []
        for job in collected:
            profile = self.extractor.job(job.description, identifier=job.identifier, title=job.title)
            result = self.scorer.score(candidate, profile)
            LOGGER.info("Scored posting %s at %.2f%%", job.identifier, result.score * 100)
            if result.selected:
                selected.append(replace(job, similarity=result.score))
            if len(selected) >= self.settings.maximum_jobs:
                break
        self.job_repository.replace(selected)
        LOGGER.info("Persisted %d selected jobs", len(selected))
        return selected

    def _load_feedback(self) -> FeedbackSnapshot:
        try:
            snapshot = self.rating_repository.load_snapshot()
            LOGGER.info(
                "Loaded %d positive and %d negative job ratings",
                len(snapshot.great),
                len(snapshot.bad),
            )
            return snapshot
        except Exception as error:
            LOGGER.error(
                "Feedback ratings are unavailable; filtering is disabled (%s)",
                type(error).__name__,
            )
            return FeedbackSnapshot()

    def _filter_bad_feedback(
        self,
        jobs: list[ScrapedJob],
        snapshot: FeedbackSnapshot,
    ) -> list[ScrapedJob]:
        if snapshot.count < self.settings.feedback_minimum_ratings:
            LOGGER.info(
                "Feedback filtering skipped: %d of %d required ratings",
                snapshot.count,
                self.settings.feedback_minimum_ratings,
            )
            return jobs
        feedback_jobs = [
            RatedJob(
                identifier=stable_job_identifier(
                    job.identifier,
                    title=job.title,
                    description=job.description,
                    location=job.location,
                ),
                title=job.title,
                description=job.description,
                location=job.location,
            )
            for job in jobs
        ]
        try:
            scorer = self.feedback_scorer or FeedbackSimilarityScorer(
                minimum_ratings=self.settings.feedback_minimum_ratings,
                bad_threshold=self.settings.bad_feedback_threshold,
            )
            decisions = scorer.score_batch(feedback_jobs, snapshot)
        except Exception as error:
            LOGGER.error(
                "Feedback similarity failed; collected jobs are preserved (%s)",
                type(error).__name__,
            )
            return jobs

        retained: list[ScrapedJob] = []
        for job, feedback_job in zip(jobs, feedback_jobs):
            decision = decisions[feedback_job.identifier]
            if decision.discarded:
                LOGGER.info(
                    "Discarded posting %s from %.2f%% negative-feedback similarity",
                    feedback_job.identifier,
                    decision.bad_similarity * 100,
                )
            else:
                retained.append(job)
        LOGGER.info("Feedback filter retained %d of %d collected jobs", len(retained), len(jobs))
        return retained
