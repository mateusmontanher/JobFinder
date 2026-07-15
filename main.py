"""CLI entry point for offline JobFinder matching."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from jobfinder.logging_config import configure_logging
from jobfinder.parsing import iter_jobs, read_resume, write_results
from jobfinder.service import JobMatchingService

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank JSONL jobs against a JSON resume locally.")
    parser.add_argument("--resume", required=True, type=Path, help="JSON file containing a 'resume' string")
    parser.add_argument("--jobs", required=True, type=Path, help="JSONL input file of jobs")
    parser.add_argument("--out", required=True, type=Path, help="Filtered JSONL output file")
    return parser


def run(resume_path: Path, jobs_path: Path, output_path: Path) -> int:
    resume = read_resume(resume_path)
    if resume is None:
        return 2
    jobs = list(iter_jobs(jobs_path))
    matches = JobMatchingService().match(resume, jobs)
    if not write_results(output_path, (match.to_record() for match in matches)):
        return 3
    LOGGER.info("Processed %d jobs and wrote %d matches", len(jobs), len(matches))
    return 0


def main() -> int:
    configure_logging()
    try:
        arguments = build_parser().parse_args()
        return run(arguments.resume, arguments.jobs, arguments.out)
    except Exception as error:  # Last-resort guard: CLI errors must be logged, never uncaught.
        LOGGER.error("Unexpected matching failure was contained (%s)", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
