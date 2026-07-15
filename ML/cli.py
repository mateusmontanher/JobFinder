"""Local command-line interface for the complete matching pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

from jobfinder.i18n import BabelPoCatalogRepository, TranslationService

from .extraction import SpacyProfileExtractor
from .matching import CompatibilityScorer, MatchConfig
from .repositories import FileResumeRepository, iter_job_records, job_fields

LOGGER = logging.getLogger("jobfinder.ml")


def build_parser(translations: TranslationService | None = None) -> argparse.ArgumentParser:
    service = translations or TranslationService(
        BabelPoCatalogRepository(Path(__file__).resolve().parents[1] / "locales")
    )
    _ = service.gettext
    parser = argparse.ArgumentParser(description=_("Match a local resume against local job records"))
    parser.add_argument("--resume", type=Path, required=True, help=_("JSON, CSV, TXT, or DOCX resume"))
    parser.add_argument("--jobs", type=Path, required=True, help=_("Job file or local folder"))
    parser.add_argument("--output", type=Path, required=True, help=_("JSON or CSV report"))
    parser.add_argument("--threshold", type=float, default=60.0, help=_("Minimum score percentage"))
    return parser


def run(resume_path: Path, jobs_path: Path, output_path: Path, threshold: float = 60.0) -> int:
    if not 0 <= threshold <= 100:
        raise ValueError("threshold must be between 0 and 100")
    extractor = SpacyProfileExtractor()
    scorer = CompatibilityScorer(MatchConfig(threshold=threshold / 100))
    candidate = extractor.candidate(FileResumeRepository(resume_path).load_text())
    selected: list[dict] = []
    processed = 0
    for processed, record in enumerate(iter_job_records(jobs_path), start=1):
        identifier, title, description = job_fields(record, processed)
        if not title and not description:
            LOGGER.error("Skipping job %s without title or description", identifier)
            continue
        result = scorer.score(candidate, extractor.job(description, identifier=identifier, title=title, raw=record))
        if result.selected:
            selected.append({**record, "id": identifier, "title": title, **result.to_dict()})
    selected.sort(key=lambda item: item["compatibility_score"], reverse=True)
    _write_report(output_path, selected)
    LOGGER.info("Processed %d jobs and selected %d", processed, len(selected))
    return 0


def _write_report(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.casefold() == ".csv":
        rows = [{key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()} for row in records]
        fields = list(dict.fromkeys(key for row in rows for key in row))
        with path.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    elif path.suffix.casefold() == ".json":
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        raise ValueError("output must use .json or .csv")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    try:
        return run(args.resume, args.jobs, args.output, args.threshold)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Matching failed safely (%s)", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
