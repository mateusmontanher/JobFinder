from __future__ import annotations

import time

import pytest
import spacy

from ML.feedback import FeedbackSimilarityScorer, FeedbackWeights
from jobfinder.feedback import FeedbackSnapshot, RatedJob


def rated(identifier, *, title="Title", description="Description", location="Remote"):
    return RatedJob(identifier, title, description, location)


def test_feedback_is_skipped_below_25_total_ratings():
    scorer = FeedbackSimilarityScorer(spacy.blank("en"))
    snapshot = FeedbackSnapshot(bad=tuple(rated(f"bad:{index}") for index in range(24)))

    decision = scorer.score_batch([rated("new:1")], snapshot)["new:1"]

    assert not decision.applied
    assert not decision.discarded
    assert decision.bad_similarity == 0


def test_exact_description_reaches_the_60_percent_rejection_boundary():
    scorer = FeedbackSimilarityScorer(spacy.blank("en"))
    bad = [
        rated("bad:0", title="Different", description="Exact shared description", location="Elsewhere"),
        *(rated(f"bad:{index}", title=f"Other {index}", description=f"Unrelated {index}", location="Office") for index in range(1, 25)),
    ]
    snapshot = FeedbackSnapshot(bad=tuple(bad))
    job = rated("new:1", title="Candidate", description="Exact shared description", location="Remote")

    decision = scorer.score_batch([job], snapshot)[job.identifier]

    assert decision.applied
    assert decision.bad_similarity == pytest.approx(0.60)
    assert decision.discarded


def test_maximum_great_and_bad_scores_are_calculated_independently():
    scorer = FeedbackSimilarityScorer(spacy.blank("en"), minimum_ratings=2)
    job = rated("new:1", title="Data Engineer", description="Python SQL", location="Remote")
    snapshot = FeedbackSnapshot(
        great=(rated("great:1", title="Data Engineer", description="Python SQL", location="Remote"),),
        bad=(rated("bad:1", title="Nurse", description="Patient care", location="Hospital"),),
    )

    decision = scorer.score_batch([job], snapshot)[job.identifier]

    assert decision.great_similarity == pytest.approx(1.0)
    assert decision.bad_similarity < 0.60
    assert not decision.discarded


@pytest.mark.performance
def test_feedback_classifies_100_jobs_with_25_ratings_under_local_budget():
    scorer = FeedbackSimilarityScorer(spacy.blank("en"))
    jobs = [rated(f"new:{index}", title=f"Engineer {index}", description=f"Python SQL project {index}") for index in range(100)]
    snapshot = FeedbackSnapshot(
        bad=tuple(rated(f"bad:{index}", title=f"Role {index}", description=f"Unrelated domain {index}") for index in range(25))
    )

    started = time.perf_counter()
    decisions = scorer.score_batch(jobs, snapshot)
    elapsed = time.perf_counter() - started

    assert len(decisions) == 100
    assert elapsed < 5.0


def test_feedback_configuration_validation():
    with pytest.raises(ValueError):
        FeedbackWeights(description=1, title=1, location=0)
    with pytest.raises(ValueError):
        FeedbackSimilarityScorer(spacy.blank("en"), bad_threshold=1.1)
