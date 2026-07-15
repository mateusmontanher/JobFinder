"""SQLite adapter for durable local job feedback."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from platformdirs import user_data_path

from .domain import FeedbackSnapshot, RatedJob, Rating, VALID_RATINGS

GREAT_TABLE = "great_jobs_openings"
BAD_TABLE = "bad_jobs_openings"
TABLE_FOR_RATING: dict[str, str] = {"great": GREAT_TABLE, "bad": BAD_TABLE}


def default_feedback_db_path() -> Path:
    return user_data_path("JobFinder", appauthor=False, ensure_exists=False) / "feedback.sqlite3"


class SQLiteRatingRepository:
    """One short-lived SQLite connection per operation for safe thread use."""

    def __init__(self, database_path: str | Path | None = None, *, timeout_seconds: float = 5.0) -> None:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 0 and 60")
        self.database_path = Path(database_path) if database_path is not None else default_feedback_db_path()
        self.timeout_seconds = timeout_seconds
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            for table in (GREAT_TABLE, BAD_TABLE):
                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        location TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (
                            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        )
                    )
                    """
                )

    def load_snapshot(self) -> FeedbackSnapshot:
        with self._connect() as connection:
            great = self._load_table(connection, GREAT_TABLE)
            bad = self._load_table(connection, BAD_TABLE)
        return FeedbackSnapshot(great=great, bad=bad)

    @staticmethod
    def _load_table(connection: sqlite3.Connection, table: str) -> tuple[RatedJob, ...]:
        rows = connection.execute(
            f"SELECT id, title, description, location FROM {table} ORDER BY created_at, id"
        ).fetchall()
        return tuple(
            RatedJob(
                identifier=row["id"],
                title=row["title"],
                description=row["description"],
                location=row["location"],
            )
            for row in rows
        )

    def set_rating(self, job: RatedJob, rating: Rating) -> None:
        if rating not in VALID_RATINGS:
            raise ValueError("rating must be 'great' or 'bad'")
        selected_table = TABLE_FOR_RATING[rating]
        opposite_table = BAD_TABLE if rating == "great" else GREAT_TABLE
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"DELETE FROM {opposite_table} WHERE id = ?", (job.identifier,))
            connection.execute(
                f"""
                INSERT INTO {selected_table} (id, title, description, location)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    description = excluded.description,
                    location = excluded.location,
                    created_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (job.identifier, job.title, job.description, job.location),
            )

    def remove_rating(self, identifier: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"DELETE FROM {GREAT_TABLE} WHERE id = ?", (identifier,))
            connection.execute(f"DELETE FROM {BAD_TABLE} WHERE id = ?", (identifier,))
