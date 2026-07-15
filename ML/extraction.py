"""Local spaCy-based profile extraction with deterministic rule fallbacks."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

import spacy
from spacy.language import Language

from .domain import CandidateProfile, JobProfile

SPACE_RE = re.compile(r"\s+")
EXPERIENCE_RE = re.compile(
    r"(?P<years>\d{1,2}(?:[.,]\d)?)\s*\+?\s*(?:years?|anos?)\s+(?:(?:of|de)\s+)?(?:experience|experi[eê]ncia)",
    re.IGNORECASE,
)

# Canonical terms and aliases cover common Portuguese/English job text locally.
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("python",), "sql": ("sql",), "java": ("java",),
    "javascript": ("javascript", "typescript", "node.js", "nodejs"),
    "machine learning": ("machine learning", "aprendizado de maquina", "aprendizado de máquina"),
    "data analysis": ("data analysis", "analise de dados", "análise de dados"),
    "data engineering": ("data engineering", "data engineer", "engenharia de dados", "engenheiro de dados", "engenheira de dados"),
    "project management": ("project management", "gestao de projetos", "gestão de projetos"),
    "renewable energy": ("renewable energy", "energia renovavel", "energia renovável", "solar", "wind power", "energia eolica", "energia eólica"),
    "electrical engineering": ("electrical engineering", "engenharia eletrica", "engenharia elétrica"),
    "mechanical engineering": ("mechanical engineering", "engenharia mecanica", "engenharia mecânica"),
}
TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "postgresql": ("postgresql", "postgres"), "power bi": ("power bi", "powerbi"),
    "excel": ("excel",), "docker": ("docker",), "kubernetes": ("kubernetes", "k8s"),
    "aws": ("aws", "amazon web services"), "azure": ("azure",),
    "git": ("git", "github", "gitlab"), "spacy": ("spacy",),
    "playwright": ("playwright",), "autocad": ("autocad",),
}
EDUCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "doctorate": ("phd", "doctorate", "doutorado"),
    "master": ("master's", "masters", "mestrado"),
    "bachelor": ("bachelor", "bachelor's", "graduacao", "graduação", "bacharelado"),
    "technical": ("technical degree", "curso tecnico", "curso técnico", "tecnologo", "tecnólogo"),
}
SENIORITY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lead", ("lead", "leader", "lider", "líder", "principal", "staff")),
    ("senior", ("senior", "sênior", "sr.")),
    ("mid", ("mid-level", "pleno", "intermediate")),
    ("junior", ("junior", "júnior", "jr.", "entry level")),
    ("intern", ("intern", "internship", "estagio", "estágio", "trainee")),
)
GENERIC_TERMS = frozenset({
    "and", "the", "for", "with", "from", "job", "work", "role", "team", "experience",
    "para", "com", "uma", "que", "por", "vaga", "trabalho", "empresa", "experiencia", "experiência",
})


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    return SPACE_RE.sub(" ", normalized).strip()


class SpacyProfileExtractor:
    """Extract structured signals using spaCy tokenization plus local phrase rules."""

    def __init__(self, nlp: Language | None = None) -> None:
        self._nlp = nlp or self._load_model()

    @staticmethod
    def _load_model() -> Language:
        for model in ("pt_core_news_md", "pt_core_news_sm"):
            try:
                return spacy.load(model, disable=("parser",))
            except (OSError, ImportError):
                continue
        return spacy.blank("pt")

    def candidate(self, text: str) -> CandidateProfile:
        return CandidateProfile(**self._signals(text))

    def job(self, text: str, *, identifier: str = "", title: str = "", raw=None) -> JobProfile:
        signals = self._signals(f"{title}\n{text}")
        return JobProfile(identifier=identifier, title=title, raw=raw or {}, **signals)

    def keywords(self, text: str, limit: int = 10) -> list[tuple[str, int]]:
        profile = self.candidate(text)
        structured = sorted(profile.skills | profile.tools)
        doc = self._nlp(normalize_text(text))
        counts = Counter(
            token.lemma_.casefold() if token.lemma_ else token.text.casefold()
            for token in doc
            if token.is_alpha and not token.is_stop and len(token.text) > 2
            and token.text.casefold() not in GENERIC_TERMS
        )
        for term in structured:
            counts[term] += max(counts.values(), default=0) + 1
        return counts.most_common(limit)

    def _signals(self, text: str) -> dict:
        normalized = normalize_text(text)
        years = [float(match.group("years").replace(",", ".")) for match in EXPERIENCE_RE.finditer(normalized)]
        return {
            "text": text or "",
            "skills": frozenset(self._phrases(normalized, SKILL_ALIASES)),
            "tools": frozenset(self._phrases(normalized, TOOL_ALIASES)),
            "education": frozenset(self._phrases(normalized, EDUCATION_ALIASES)),
            "years_experience": max(years) if years else None,
            "seniority": self._seniority(normalized),
        }

    @staticmethod
    def _phrases(text: str, aliases: dict[str, tuple[str, ...]]) -> Iterable[str]:
        for canonical, variants in aliases.items():
            if any(re.search(rf"(?<!\w){re.escape(variant)}(?!\w)", text) for variant in variants):
                yield canonical

    @staticmethod
    def _seniority(text: str) -> str | None:
        for level, aliases in SENIORITY_ALIASES:
            if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text) for alias in aliases):
                return level
        return None
