"""Offline job-to-resume matching application."""

from .domain import Job, MatchResult, Resume
from .service import JobMatchingService

__all__ = ["Job", "MatchResult", "Resume", "JobMatchingService"]
