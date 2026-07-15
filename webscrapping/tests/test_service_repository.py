import spacy

from ML.extraction import SpacyProfileExtractor
from ML.feedback import FeedbackSimilarityScorer
from jobfinder.feedback import FeedbackSnapshot, RatedJob
from webscrapping.domain import ScrapedJob, SearchSettings
from webscrapping.repositories import PostgresJobRepository
from webscrapping.service import JobSearchService


class ResumeRepo:
    def load_text(self): return "Senior Python SQL engineer with 8 years of experience"


class JobRepo:
    def __init__(self): self.jobs = None
    def replace(self, jobs): self.jobs = list(jobs); return len(self.jobs)


class RatingRepo:
    def __init__(self, snapshot=FeedbackSnapshot()): self.snapshot, self.calls = snapshot, 0
    def load_snapshot(self): self.calls += 1; return self.snapshot


class Collector:
    def collect(self, url):
        assert "keywords=" in url
        return [
            ScrapedJob("1", "Senior Python Engineer", "A", "Remote", "Python SQL, 5 years of experience", "url-1"),
            ScrapedJob("2", "Mechanical Engineer", "B", "Remote", "Mechanical engineering AutoCAD", "url-2"),
        ]


class RetryingCollector(Collector):
    def __init__(self): self.calls = 0
    def collect(self, url):
        self.calls += 1
        return [] if self.calls < 3 else super().collect(url)


class EmptyCollector:
    def __init__(self): self.calls = 0
    def collect(self, url): self.calls += 1; return []


def test_service_persists_only_relevant_jobs():
    repository = JobRepo()
    service = JobSearchService(
        resume_repository=ResumeRepo(), job_repository=repository, collector=Collector(),
        extractor=SpacyProfileExtractor(spacy.blank("pt")),
        rating_repository=RatingRepo(),
    )
    selected = service.run()
    assert [job.identifier for job in selected] == ["1"]
    assert repository.jobs == selected


def test_service_broadens_query_after_empty_results():
    repository = JobRepo()
    collector = RetryingCollector()
    service = JobSearchService(
        resume_repository=ResumeRepo(), job_repository=repository, collector=collector,
        extractor=SpacyProfileExtractor(spacy.blank("pt")),
        rating_repository=RatingRepo(),
    )
    assert [job.identifier for job in service.run()] == ["1"]
    assert collector.calls == 3


def test_empty_search_preserves_existing_database_jobs():
    repository = JobRepo()
    collector = EmptyCollector()
    service = JobSearchService(
        resume_repository=ResumeRepo(), job_repository=repository, collector=collector,
        extractor=SpacyProfileExtractor(spacy.blank("pt")),
        rating_repository=RatingRepo(),
    )
    assert service.run() == []
    assert collector.calls == 3
    assert repository.jobs is None


class Cursor:
    def __init__(self, rows=()): self.calls, self.rows = [], list(rows)
    def execute(self, query, params=None): self.calls.append((" ".join(query.split()), params))
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *args): pass


class Connection:
    def __init__(self, rows=()): self.cursor_value = Cursor(rows)
    def cursor(self): return self.cursor_value
    def __enter__(self): return self
    def __exit__(self, *args): pass


def test_postgres_repository_replaces_in_single_connection():
    connection = Connection()
    repository = PostgresJobRepository(connect=lambda: connection)
    count = repository.replace([ScrapedJob("1", "Title", "Company", "Remote", "Text", "url", similarity=.8)])
    assert count == 1
    assert connection.cursor_value.calls[0][0] == "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_id TEXT"
    assert connection.cursor_value.calls[1][0] == "TRUNCATE TABLE jobs RESTART IDENTITY"
    assert "INSERT INTO jobs" in connection.cursor_value.calls[2][0]
    assert connection.cursor_value.calls[2][1][0] == "1"


def test_postgres_repository_reads_stable_jobs_for_frontends():
    connection = Connection(rows=[(
        "linkedin:42", "Data Engineer", "Acme", "Remote", "Python SQL",
        "https://example.com/42", "", 0.82,
    )])
    repository = PostgresJobRepository(connect=lambda: connection)

    jobs = repository.list_jobs()

    assert len(jobs) == 1
    assert jobs[0].identifier == "linkedin:42"
    assert jobs[0].similarity == 0.82
    assert "SELECT source_id" in connection.cursor_value.calls[1][0]
    assert repository.get("linkedin:42").title == "Data Engineer"
    assert repository.get("missing") is None


def test_feedback_snapshot_loads_once_and_filters_exact_dislike():
    bad_jobs = tuple(
        RatedJob(
            f"bad:{index}",
            "Senior Python Engineer",
            "Python SQL, 5 years of experience",
            "Remote",
        )
        for index in range(25)
    )
    ratings = RatingRepo(FeedbackSnapshot(bad=bad_jobs))
    repository = JobRepo()
    service = JobSearchService(
        resume_repository=ResumeRepo(),
        job_repository=repository,
        collector=Collector(),
        extractor=SpacyProfileExtractor(spacy.blank("pt")),
        rating_repository=ratings,
        feedback_scorer=FeedbackSimilarityScorer(spacy.blank("en")),
        settings=SearchSettings(feedback_minimum_ratings=25),
    )

    assert service.run() == []
    assert repository.jobs == []
    assert ratings.calls == 1


def test_feedback_similarity_is_not_called_below_25_ratings():
    class UnexpectedScorer:
        def score_batch(self, jobs, snapshot):
            raise AssertionError("feedback scorer must be skipped")

    ratings = RatingRepo(
        FeedbackSnapshot(
            bad=tuple(RatedJob(f"bad:{index}", "Title", "Description", "Remote") for index in range(24))
        )
    )
    service = JobSearchService(
        resume_repository=ResumeRepo(),
        job_repository=JobRepo(),
        collector=Collector(),
        extractor=SpacyProfileExtractor(spacy.blank("pt")),
        rating_repository=ratings,
        feedback_scorer=UnexpectedScorer(),
    )

    assert [job.identifier for job in service.run()] == ["1"]
    assert ratings.calls == 1
