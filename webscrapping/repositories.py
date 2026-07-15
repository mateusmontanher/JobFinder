"""PostgreSQL persistence for collected jobs."""

from __future__ import annotations

import os
from collections.abc import Iterable

from jobfinder.feedback.domain import stable_job_identifier

from .domain import ScrapedJob


def postgres_connect():
    import psycopg2
    return psycopg2.connect(
        database=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
    )


class PostgresJobRepository:
    def __init__(self, connect=None) -> None:
        self._connect = connect or postgres_connect

    def replace(self, jobs: Iterable[ScrapedJob]) -> int:
        records = list(jobs)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._ensure_source_identifier(cursor)
                cursor.execute("TRUNCATE TABLE jobs RESTART IDENTITY")
                for job in records:
                    cursor.execute(
                        """
                        INSERT INTO jobs (
                            source_id, company_name, job_title, description, locate, url,
                            company_logo_path, similarity
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            stable_job_identifier(
                                job.identifier,
                                title=job.title,
                                description=job.description,
                                location=job.location,
                            ),
                            job.company,
                            job.title,
                            job.description,
                            job.location,
                            job.url,
                            job.logo,
                            job.similarity,
                        ),
                    )
        return len(records)

    def list_jobs(self) -> list[ScrapedJob]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._ensure_source_identifier(cursor)
                cursor.execute(
                    """
                    SELECT source_id, job_title, company_name, locate, description,
                           url, company_logo_path, similarity
                    FROM jobs
                    ORDER BY similarity DESC NULLS LAST, id
                    """
                )
                rows = cursor.fetchall()
        jobs: list[ScrapedJob] = []
        for source_id, title, company, location, description, url, logo, similarity in rows:
            identifier = stable_job_identifier(
                source_id,
                title=title or "",
                description=description or "",
                location=location or "",
            )
            jobs.append(
                ScrapedJob(
                    identifier=identifier,
                    title=title or "",
                    company=company or "",
                    location=location or "",
                    description=description or "",
                    url=url or "",
                    logo=logo or "",
                    similarity=float(similarity or 0.0),
                )
            )
        return jobs

    def get(self, identifier: str) -> ScrapedJob | None:
        return next((job for job in self.list_jobs() if job.identifier == identifier), None)

    @staticmethod
    def _ensure_source_identifier(cursor) -> None:
        cursor.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_id TEXT")
