"""Scraping-domain values independent of Playwright and PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScrapedJob:
    identifier: str
    title: str
    company: str
    location: str
    description: str
    url: str
    logo: str = ""
    similarity: float = 0.0


@dataclass(frozen=True)
class SearchSettings:
    locations: tuple[str, ...] = ("United States", "Canada", "United Kingdom")
    work_types: tuple[str, ...] = ("1", "3")
    experience_levels: tuple[str, ...] = ("4", "5", "6")
    maximum_jobs: int = 60
    minimum_similarity: float = 0.60
    feedback_minimum_ratings: int = 25
    bad_feedback_threshold: float = 0.60
