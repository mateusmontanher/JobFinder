"""Batched local spaCy similarity for user-rated job openings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import spacy
from spacy.language import Language
from spacy.tokens import Doc

from jobfinder.feedback.domain import FeedbackSnapshot, RatedJob

from .extraction import normalize_text


@dataclass(frozen=True, slots=True)
class FeedbackWeights:
    description: float = 0.60
    title: float = 0.30
    location: float = 0.10

    def __post_init__(self) -> None:
        values = (self.description, self.title, self.location)
        if any(value < 0 for value in values) or not np.isclose(sum(values), 1.0):
            raise ValueError("feedback weights must be non-negative and sum to 1.0")


@dataclass(frozen=True, slots=True)
class FeedbackDecision:
    identifier: str
    great_similarity: float
    bad_similarity: float
    discarded: bool
    applied: bool


class FeedbackSimilarityScorer:
    """Compare jobs to each rating and retain the strongest set similarity."""

    def __init__(
        self,
        nlp: Language | None = None,
        *,
        weights: FeedbackWeights | None = None,
        minimum_ratings: int = 25,
        bad_threshold: float = 0.60,
    ) -> None:
        if minimum_ratings < 0:
            raise ValueError("minimum_ratings must be non-negative")
        if not 0 <= bad_threshold <= 1:
            raise ValueError("bad_threshold must be between 0 and 1")
        self._nlp = nlp or self._load_model()
        self.weights = weights or FeedbackWeights()
        self.minimum_ratings = minimum_ratings
        self.bad_threshold = bad_threshold

    @staticmethod
    def _load_model() -> Language:
        for model in ("en_core_web_md", "en_core_web_sm"):
            try:
                return spacy.load(model, disable=("parser", "ner"))
            except (OSError, ImportError):
                continue
        return spacy.blank("en")

    def score_batch(
        self,
        jobs: list[RatedJob],
        snapshot: FeedbackSnapshot,
    ) -> dict[str, FeedbackDecision]:
        if snapshot.count < self.minimum_ratings:
            return {
                job.identifier: FeedbackDecision(job.identifier, 0.0, 0.0, False, False)
                for job in jobs
            }
        references = [*snapshot.great, *snapshot.bad]
        if not jobs or not references:
            return {}

        weighted = np.zeros((len(jobs), len(references)), dtype=float)
        for field, weight in (
            ("description", self.weights.description),
            ("title", self.weights.title),
            ("location", self.weights.location),
        ):
            new_values = [getattr(job, field) for job in jobs]
            rated_values = [getattr(job, field) for job in references]
            weighted += weight * self._field_similarity(new_values, rated_values)

        great_count = len(snapshot.great)
        decisions: dict[str, FeedbackDecision] = {}
        for index, job in enumerate(jobs):
            great_score = float(weighted[index, :great_count].max()) if great_count else 0.0
            bad_score = float(weighted[index, great_count:].max()) if snapshot.bad else 0.0
            decisions[job.identifier] = FeedbackDecision(
                identifier=job.identifier,
                great_similarity=max(0.0, min(1.0, great_score)),
                bad_similarity=max(0.0, min(1.0, bad_score)),
                discarded=bad_score >= self.bad_threshold,
                applied=True,
            )
        return decisions

    def _field_similarity(self, left: list[str], right: list[str]) -> np.ndarray:
        left_normalized = [normalize_text(value) for value in left]
        right_normalized = [normalize_text(value) for value in right]
        documents = list(self._nlp.pipe([*left_normalized, *right_normalized], batch_size=64))
        left_docs = documents[: len(left)]
        right_docs = documents[len(left) :]
        matrix = self._semantic_similarity(left_docs, right_docs)

        for row, left_value in enumerate(left_normalized):
            for column, right_value in enumerate(right_normalized):
                if left_value and left_value == right_value:
                    matrix[row, column] = 1.0
                elif matrix[row, column] == 0.0:
                    matrix[row, column] = self._token_jaccard(left_docs[row], right_docs[column])
        return matrix

    @staticmethod
    def _semantic_similarity(left: list[Doc], right: list[Doc]) -> np.ndarray:
        if not left or not right or not left[0].vector.size:
            return np.zeros((len(left), len(right)), dtype=float)
        left_vectors = np.vstack([doc.vector for doc in left]).astype(float, copy=False)
        right_vectors = np.vstack([doc.vector for doc in right]).astype(float, copy=False)
        left_norms = np.linalg.norm(left_vectors, axis=1, keepdims=True)
        right_norms = np.linalg.norm(right_vectors, axis=1, keepdims=True)
        denominator = left_norms @ right_norms.T
        similarities = np.divide(
            left_vectors @ right_vectors.T,
            denominator,
            out=np.zeros_like(denominator),
            where=denominator > 0,
        )
        return np.clip(similarities, 0.0, 1.0)

    @staticmethod
    def _token_jaccard(left: Doc, right: Doc) -> float:
        left_terms = {token.text for token in left if not token.is_space and not token.is_stop}
        right_terms = {token.text for token in right if not token.is_space and not token.is_stop}
        union = left_terms | right_terms
        return len(left_terms & right_terms) / len(union) if union else 0.0
