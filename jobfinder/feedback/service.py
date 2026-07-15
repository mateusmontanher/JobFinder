"""Feedback use cases independent of SQLite and presentation details."""

from __future__ import annotations

from .domain import FeedbackSnapshot, RatedJob, Rating, VALID_RATINGS
from .ports import RatingRepository


class RatingService:
    def __init__(self, repository: RatingRepository) -> None:
        self.repository = repository

    def snapshot(self) -> FeedbackSnapshot:
        return self.repository.load_snapshot()

    def rate(self, job: RatedJob, rating: Rating) -> None:
        if rating not in VALID_RATINGS:
            raise ValueError("rating must be 'great' or 'bad'")
        self.repository.set_rating(job, rating)

    def clear(self, identifier: str) -> None:
        self.repository.remove_rating(identifier)
