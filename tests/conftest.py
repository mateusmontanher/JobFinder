from __future__ import annotations

import pytest
from pathlib import Path
from uuid import uuid4

from UI.api import LocalApiServer
from jobfinder.feedback import RatingService, SQLiteRatingRepository
from webscrapping.domain import ScrapedJob


class MemoryJobs:
    def __init__(self):
        self.job = ScrapedJob(
            "linkedin:123",
            "Senior Python Engineer",
            "Acme Energy",
            "Remote",
            "Build reliable Python and SQL services.\nWork with a distributed engineering team.",
            "https://example.com/jobs/123",
            similarity=0.82,
        )

    def list_jobs(self):
        return [self.job]

    def get(self, identifier):
        return self.job if identifier == self.job.identifier else None


@pytest.fixture
def runtime_database():
    path = Path("ML/tests/runtime") / f"feedback-{uuid4().hex}.sqlite3"
    yield path
    for candidate in path.parent.glob(f"{path.name}*"):
        try:
            candidate.unlink()
        except OSError:
            pass


@pytest.fixture
def api_environment(runtime_database):
    ratings = RatingService(SQLiteRatingRepository(runtime_database))
    jobs = MemoryJobs()
    with LocalApiServer(jobs, ratings, static_directory="UI/static") as server:
        yield server, ratings, jobs


def pytest_addoption(parser):
    parser.addoption("--run-browser", action="store_true", help="run Playwright browser tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-browser"):
        return
    skip = pytest.mark.skip(reason="use --run-browser to run browser integration tests")
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)
