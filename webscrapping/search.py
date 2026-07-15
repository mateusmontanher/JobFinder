"""Deterministic LinkedIn search-query construction."""

from __future__ import annotations

from urllib.parse import urlencode

from .domain import SearchSettings


def build_search_url(
    keywords: list[tuple[str, int]] | list[str],
    settings: SearchSettings | None = None,
    *,
    term_limit: int = 3,
) -> str:
    settings = settings or SearchSettings()
    terms = [item[0] if isinstance(item, tuple) else item for item in keywords]
    unique_terms = list(dict.fromkeys(term.strip().casefold() for term in terms if term and term.strip()))
    if not unique_terms:
        raise ValueError("at least one resume keyword is required")
    if term_limit < 1:
        raise ValueError("term_limit must be at least 1")
    params = {
        "keywords": " ".join(unique_terms[:term_limit]),
        "location": settings.locations[0],
        "f_wt": ",".join(settings.work_types),
        "f_E": ",".join(settings.experience_levels),
    }
    return f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"
