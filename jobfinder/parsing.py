"""Fault-tolerant input and output adapters."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator, Mapping

from .domain import Job, Resume

LOGGER = logging.getLogger(__name__)


def read_resume(path: Path) -> Resume | None:
    """Return a resume from ``{"resume": "..."}``, or ``None`` on bad input."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        LOGGER.error("Could not read resume input (%s)", type(error).__name__)
        return None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("resume"), str):
        LOGGER.error("Resume input must contain a string 'resume' key")
        return None
    text = payload["resume"].strip()
    if not text:
        LOGGER.error("Resume input contains an empty resume")
        return None
    return Resume(text=text)


def iter_jobs(path: Path) -> Iterator[Job]:
    """Yield valid jobs and log malformed JSONL records without stopping."""
    try:
        source = path.open("r", encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        LOGGER.error("Could not open jobs input (%s)", type(error).__name__)
        return
    with source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                LOGGER.warning("Skipping invalid JSON job record %d", line_number)
                continue
            job = job_from_record(payload, line_number)
            if job is None:
                LOGGER.warning("Skipping invalid job record %d", line_number)
                continue
            yield job


def job_from_record(payload: Any, line_number: int = 0) -> Job | None:
    """Map common scraper field names to a Job without raising."""
    if not isinstance(payload, Mapping):
        return None
    title = first_string(payload, "title", "job_title", "name")
    description = first_string(payload, "description", "job_text", "summary")
    if not title and not description:
        return None
    identifier = first_string(payload, "id", "job_id", "url", "link") or f"line-{line_number}"
    return Job(identifier=identifier, title=title, description=description, raw=dict(payload))


def write_results(path: Path, records: Iterator[dict[str, Any]]) -> bool:
    """Write JSONL atomically enough for normal local failures."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as destination:
            for record in records:
                destination.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as error:
        LOGGER.error("Could not write matching output (%s)", type(error).__name__)
        return False
    return True


def first_string(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""
