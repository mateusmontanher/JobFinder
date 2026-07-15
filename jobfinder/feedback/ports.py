"""Dependency-inversion ports for feedback persistence."""

from __future__ import annotations

from typing import Protocol

from .domain import FeedbackSnapshot, RatedJob, Rating


class RatingRepository(Protocol):
    def load_snapshot(self) -> FeedbackSnapshot: ...

    def set_rating(self, job: RatedJob, rating: Rating) -> None: ...

    def remove_rating(self, identifier: str) -> None: ...
