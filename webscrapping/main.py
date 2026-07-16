"""Backward-compatible facade for the job-search application service."""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from .collector import is_the_title_in_blacklist, normalize_title
from .search import build_search_url
from .service import JobSearchService

LOGGER = logging.getLogger(__name__)


class JobSearchFailed(RuntimeError):
    """Raised when a search cannot run, instead of presenting stale saved jobs as new."""


def resource_path(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    return os.path.join(base, *parts)


load_dotenv(resource_path(".env") if os.path.exists(resource_path(".env")) else None)


def generate_url(errors: int = 0, search_error: str = "") -> str:
    """Legacy URL API; failed URLs are no longer mixed into query generation."""
    del errors, search_error
    from ML.main import KeyWords
    return build_search_url(KeyWords())


def create_url(errors: int = 0, search_error: str = "") -> str:
    return generate_url(errors, search_error)


def BrowsingForJobs() -> list:
    """Run collection and local matching without import-time side effects."""
    try:
        return JobSearchService().run()
    except Exception as error:
        LOGGER.error("Job search failed safely (%s)", type(error).__name__)
        raise JobSearchFailed("The job search could not be completed") from error


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    BrowsingForJobs()
