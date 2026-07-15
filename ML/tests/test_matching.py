import spacy
import pytest

from ML.domain import ScoreWeights
from ML.extraction import SpacyProfileExtractor
from ML.matching import CompatibilityScorer, MatchConfig


@pytest.fixture
def extractor():
    return SpacyProfileExtractor(spacy.blank("pt"))


def test_extracts_local_structured_signals(extractor):
    profile = extractor.candidate(
        "Engenheiro de dados sênior com 7 anos de experiência. Python, SQL, PostgreSQL e Power BI. Mestrado."
    )
    assert profile.skills == frozenset({"python", "sql", "data engineering"})
    assert profile.tools == frozenset({"postgresql", "power bi"})
    assert profile.education == frozenset({"master"})
    assert profile.years_experience == 7
    assert profile.seniority == "senior"


def test_relevant_job_passes_and_irrelevant_job_fails(extractor):
    candidate = extractor.candidate("Senior data engineer, 8 years of experience with Python, SQL, PostgreSQL and AWS")
    scorer = CompatibilityScorer()
    relevant = scorer.score(candidate, extractor.job("Requires 5 years of experience with Python, SQL and AWS", title="Senior Data Engineer"))
    irrelevant = scorer.score(candidate, extractor.job("Mechanical engineering role using AutoCAD", title="Mechanical Engineer"))
    assert relevant.selected
    assert relevant.score > irrelevant.score
    assert not irrelevant.selected
    assert relevant.matched_skills == ("aws", "data engineering", "python", "sql")


def test_experience_and_seniority_reduce_score(extractor):
    candidate = extractor.candidate("Junior Python developer with 1 year of experience")
    result = CompatibilityScorer().score(
        candidate, extractor.job("Python and SQL. 8 years of experience required", title="Lead developer")
    )
    assert result.components["experience"] == pytest.approx(0.125)
    assert result.components["seniority"] == 0
    assert not result.selected


def test_configuration_validation():
    with pytest.raises(ValueError):
        ScoreWeights(skills=1, experience=1, seniority=0, other=0)
    with pytest.raises(ValueError):
        MatchConfig(threshold=1.1)
