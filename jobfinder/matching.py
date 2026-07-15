"""Local, explainable resume-to-job scoring."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import spacy
from sklearn.feature_extraction.text import TfidfVectorizer

from .domain import Job, MatchResult, Resume

TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")
GENERIC_TERMS = frozenset(
    {
        "and", "the", "for", "with", "from", "that", "this", "you", "your",
        "job", "role", "work", "team", "company", "experience", "required",
        "responsibilities", "skills", "years", "para", "com", "uma", "que", "dos",
        "das", "como", "você", "vaga", "empresa", "trabalho", "experiência",
    }
)
NEGATIVE_ROLE_PAIRS = (
    ("software", "mechanical"),
    ("software", "electrical"),
    ("data", "nurse"),
    ("data", "accountant"),
    ("designer", "driver"),
)


@dataclass(frozen=True)
class MatchSettings:
    threshold: float = 0.34
    tfidf_weight: float = 0.55
    skill_weight: float = 0.30
    title_weight: float = 0.15


class ResumeMatcher:
    """Scores jobs using local TF-IDF and spaCy tokenization only."""

    def __init__(self, settings: MatchSettings | None = None) -> None:
        self.settings = settings or MatchSettings()
        self._nlp = spacy.blank("en")

    def rank(self, resume: Resume, jobs: Iterable[Job]) -> list[MatchResult]:
        job_list = list(jobs)
        if not job_list:
            return []
        resume_terms = self.terms(resume.text)
        if not resume_terms:
            return []
        documents = [resume.text, *(job.text for job in job_list)]
        lexical_scores = self._tfidf_scores(documents)
        results = [
            self.score(resume_terms, job, lexical_score)
            for job, lexical_score in zip(job_list, lexical_scores)
        ]
        return sorted(
            (result for result in results if result.score >= self.settings.threshold),
            key=lambda result: (-result.score, result.job.identifier),
        )

    def score(self, resume_terms: set[str], job: Job, lexical_score: float) -> MatchResult:
        job_terms = self.terms(job.text)
        title_terms = self.terms(job.title)
        shared = resume_terms & job_terms
        skill_score = len(shared) / max(1, len(job_terms))
        title_score = len(resume_terms & title_terms) / max(1, len(title_terms))
        score = (
            self.settings.tfidf_weight * lexical_score
            + self.settings.skill_weight * skill_score
            + self.settings.title_weight * title_score
        )
        penalties = self._role_penalty(resume_terms, job_terms)
        score = float(np.clip(score - penalties, 0.0, 1.0))
        reasons = self._reasons(shared, penalties)
        return MatchResult(job=job, score=score, reasons=reasons)

    def terms(self, text: str) -> set[str]:
        if not isinstance(text, str):
            return set()
        # spaCy supplies robust Unicode tokenization; regex preserves C++, C#, and names.
        normalized = " ".join(token.text.lower() for token in self._nlp(text) if not token.is_space)
        return {
            token.lower().strip(".-")
            for token in TOKEN_PATTERN.findall(normalized)
            if token.lower() not in GENERIC_TERMS and len(token) > 1
        }

    @staticmethod
    def _tfidf_scores(documents: list[str]) -> np.ndarray:
        try:
            matrix = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform(documents)
        except ValueError:
            return np.zeros(len(documents) - 1, dtype=float)
        resume_vector = matrix[0]
        return (matrix[1:] @ resume_vector.T).toarray().ravel()

    @staticmethod
    def _role_penalty(resume_terms: set[str], job_terms: set[str]) -> float:
        return 0.28 if any(a in resume_terms and b in job_terms for a, b in NEGATIVE_ROLE_PAIRS) else 0.0

    @staticmethod
    def _reasons(shared: set[str], penalty: float) -> tuple[str, ...]:
        reasons = [f"shared skills: {', '.join(sorted(shared)[:6])}" if shared else "limited shared skills"]
        if penalty:
            reasons.append("conflicting role signal detected")
        return tuple(reasons)
