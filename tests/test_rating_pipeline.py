from __future__ import annotations

import time

import pytest
import spacy

from ML.feedback import FeedbackSimilarityScorer
from jobfinder.feedback import FeedbackSnapshot, RatedJob
from webscrapping.collector import LinkedInCollector


class FixtureResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


def fixture_html(identifier: str) -> str:
    return f"""
        <a href="/jobs/view/{identifier}">
          <h2 class="top-card-layout__title">Data Engineer {identifier}</h2>
        </a>
        <a class="topcard__org-name-link">Company {identifier}</a>
        <span class="topcard__flavor--bullet">Remote</span>
        <div class="show-more-less-html__markup">
          Python SQL data platform responsibilities unique-{identifier}
        </div>
    """


@pytest.mark.performance
def test_100_job_collection_and_feedback_classification_meets_budget_and_omits_dislikes():
    def get(url, **_kwargs):
        identifier = url.rsplit("/", 1)[-1]
        return FixtureResponse(fixture_html(identifier))

    started = time.perf_counter()
    collected = list(
        LinkedInCollector(
            http_get=get,
            maximum_workers=4,
            minimum_request_interval_seconds=0,
        )._postings(
            [str(index) for index in range(100)]
        )
    )
    disliked = tuple(
        RatedJob(job.identifier, job.title, job.description, job.location)
        for job in collected[:25]
    )
    feedback_jobs = [
        RatedJob(job.identifier, job.title, job.description, job.location)
        for job in collected
    ]
    decisions = FeedbackSimilarityScorer(spacy.blank("en")).score_batch(
        feedback_jobs,
        FeedbackSnapshot(bad=disliked),
    )
    elapsed = time.perf_counter() - started

    omitted_dislikes = sum(decisions[job.identifier].discarded for job in disliked)
    assert len(collected) == 100
    assert omitted_dislikes / len(disliked) >= 0.90
    assert elapsed < 60
