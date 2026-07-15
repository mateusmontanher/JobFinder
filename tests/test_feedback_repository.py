from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from jobfinder.feedback import RatedJob, RatingService, SQLiteRatingRepository, stable_job_identifier


def make_job(identifier="linkedin:1"):
    return RatedJob(identifier, "Python Engineer", "Build Python services", "Remote")


def test_schema_contains_only_required_feedback_columns(runtime_database):
    path = runtime_database
    SQLiteRatingRepository(path)

    with sqlite3.connect(path) as connection:
        for table in ("great_jobs_openings", "bad_jobs_openings"):
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [column[1] for column in columns] == [
                "id", "title", "description", "location", "created_at"
            ]
            assert columns[0][5] == 1


def test_rating_switch_is_atomic_and_mutually_exclusive(runtime_database):
    service = RatingService(SQLiteRatingRepository(runtime_database))
    job = make_job()

    service.rate(job, "great")
    assert service.snapshot().rating_for(job.identifier) == "great"

    service.rate(job, "bad")
    snapshot = service.snapshot()
    assert snapshot.rating_for(job.identifier) == "bad"
    assert snapshot.great == ()

    service.clear(job.identifier)
    assert service.snapshot().rating_for(job.identifier) is None


def test_repeated_concurrent_ratings_do_not_duplicate_or_lock(runtime_database):
    service = RatingService(SQLiteRatingRepository(runtime_database))
    job = make_job()
    values = ["great", "bad"] * 10

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda value: service.rate(job, value), values))

    snapshot = service.snapshot()
    assert snapshot.count == 1
    assert snapshot.rating_for(job.identifier) in ("great", "bad")


def test_legacy_identifier_is_deterministic_and_does_not_expose_text():
    first = stable_job_identifier("", title="Private title", description="Private description", location="Home")
    second = stable_job_identifier(None, title="Private title", description="Private description", location="Home")

    assert first == second
    assert first.startswith("legacy:")
    assert "Private" not in first
    assert stable_job_identifier("linkedin:123") == "linkedin:123"


def test_sqlite_timeout_is_bounded(runtime_database):
    with pytest.raises(ValueError):
        SQLiteRatingRepository(runtime_database, timeout_seconds=0)
