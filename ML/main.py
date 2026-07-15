"""Backward-compatible facade for the local matching component."""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache

from dotenv import load_dotenv

from .extraction import SpacyProfileExtractor
from .matching import CompatibilityScorer, MatchConfig
from .repositories import PostgresResumeRepository, ResumeRepository

LOGGER = logging.getLogger(__name__)


def resource_path(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    return os.path.join(base, *parts)


load_dotenv(resource_path(".env") if os.path.exists(resource_path(".env")) else None)


@lru_cache(maxsize=1)
def _extractor() -> SpacyProfileExtractor:
    return SpacyProfileExtractor()


def _resume_text(repository: ResumeRepository | None = None) -> str:
    return (repository or PostgresResumeRepository()).load_text()


def KeyWords(repository: ResumeRepository | None = None, limit: int = 10) -> list[tuple[str, int]]:
    """Return deterministic local search terms while preserving the legacy API."""
    return _extractor().keywords(_resume_text(repository), limit=limit)


def ReturnSimilarityDetails(
    document: str,
    *,
    title: str = "",
    repository: ResumeRepository | None = None,
    threshold: float = 0.60,
):
    candidate = _extractor().candidate(_resume_text(repository))
    job = _extractor().job(document, title=title)
    return CompatibilityScorer(MatchConfig(threshold=threshold)).score(candidate, job)


def ReturnSimilatity(document: str, repository: ResumeRepository | None = None) -> float:
    """Return a 0..1 score; misspelling retained for existing scraper callers."""
    return ReturnSimilarityDetails(document, repository=repository).score


def ReturnSimilarity(document: str, repository: ResumeRepository | None = None) -> float:
    return ReturnSimilatity(document, repository)


if __name__ == "__main__":
    LOGGER.info("Use `python -m ML.cli --help` to run the local pipeline")
