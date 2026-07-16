import time

import pytest
import requests

from webscrapping.collector import (
    LinkedInCollector,
    bundled_chromium_executable,
    is_the_title_in_blacklist,
    normalize_title,
)
from webscrapping.search import build_search_url


class FakeCard:
    def __init__(self, urn): self.urn = urn
    def get_attribute(self, name): return self.urn if name == "data-entity-urn" else None


class FakeCards:
    def __init__(self, urns): self.cards = [FakeCard(urn) for urn in urns]
    def count(self): return len(self.cards)
    def nth(self, index): return self.cards[index]


class FakePage:
    def __init__(self, urns): self.cards = FakeCards(urns)
    def locator(self, selector):
        return self.cards if selector == ".job-search-card" else FakeCards([])


def test_card_identifier_structure_is_preserved():
    page = FakePage([
        "urn:li:jobPosting:123", "urn:li:jobPosting:123",
        None, "urn:li:jobPosting:not-a-number", "urn:li:jobPosting:456",
    ])
    assert LinkedInCollector.card_identifiers(page) == ["123", "456"]


def test_search_is_deterministic_and_encoded():
    first = build_search_url([("Python", 9), ("SQL", 8), ("Python", 2)])
    second = build_search_url([("Python", 9), ("SQL", 8), ("Python", 2)])
    assert first == second
    assert "keywords=python+sql" in first


def test_search_can_be_progressively_broadened():
    keywords = [("python", 3), ("sql", 2), ("aws", 1)]
    assert "keywords=python+sql+aws" in build_search_url(keywords, term_limit=3)
    assert "keywords=python" in build_search_url(keywords, term_limit=1)


def test_title_normalization_and_blacklist():
    assert normalize_title("  Senior   Engineer ") == "senior engineer"
    assert is_the_title_in_blacklist("Senior Mechanical Engineer")
    assert not is_the_title_in_blacklist("Senior Data Engineer")


class ClickableTitle:
    def __init__(self, page, fails=False): self.page, self.fails = page, fails
    def click(self, timeout):
        if self.fails:
            from playwright.sync_api import Error
            raise Error("click failed")
        self.page.url = "https://www.linkedin.com/jobs/view/123"


class PostingPage:
    def __init__(self, fails=False):
        self.url = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/123"
        self.title = ClickableTitle(self, fails)
        self.waited = None
    def locator(self, selector):
        assert selector == "xpath=/html/body/section/div/div[1]/div/a/h2"
        return self.title
    def wait_for_timeout(self, milliseconds): self.waited = milliseconds


def test_public_url_is_read_after_title_click_and_short_wait():
    page = PostingPage()
    result = LinkedInCollector._resolve_job_url(page, "fallback")
    assert result == "https://www.linkedin.com/jobs/view/123"
    assert page.waited == 1_500


def test_public_url_falls_back_when_title_click_fails():
    assert LinkedInCollector._resolve_job_url(PostingPage(fails=True), "fallback") == "fallback"


class FakeResponse:
    def __init__(self, text, *, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error


def posting_html(identifier: str, *, title: str | None = None) -> str:
    title = title or f"Data Engineer {identifier}"
    return f"""
        <section>
          <a href="/jobs/view/{identifier}"><h2 class="top-card-layout__title">{title}</h2></a>
          <a class="topcard__org-name-link">Acme</a>
          <span class="topcard__flavor--bullet">Remote</span>
          <img class="top-card-layout__entity-image" data-delayed-url="https://images.test/{identifier}.png">
          <div class="show-more-less-html__markup">Python SQL data pipelines {identifier}</div>
        </section>
    """


def test_guest_postings_are_parsed_in_card_order_with_public_urls():
    calls = []

    def get(url, **kwargs):
        identifier = url.rsplit("/", 1)[-1]
        calls.append((identifier, kwargs))
        return FakeResponse(posting_html(identifier))

    jobs = list(LinkedInCollector(http_get=get, maximum_workers=2)._postings(["2", "1"]))

    assert [job.identifier for job in jobs] == ["2", "1"]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/2"
    assert jobs[0].description == "Python SQL data pipelines 2"
    assert all(call[1]["timeout"] == 5.0 for call in calls)


def test_guest_postings_remain_deduplicated_and_blacklisted():
    titles = {"1": "Data Engineer", "2": "Data Engineer", "3": "Mechanical Engineer"}

    def get(url, **_kwargs):
        identifier = url.rsplit("/", 1)[-1]
        return FakeResponse(posting_html(identifier, title=titles[identifier]))

    jobs = list(LinkedInCollector(http_get=get)._postings(["1", "2", "3"]))
    assert [job.identifier for job in jobs] == ["1"]


@pytest.mark.performance
def test_bounded_posting_collection_runs_concurrently():
    def get(url, **_kwargs):
        time.sleep(0.05)
        identifier = url.rsplit("/", 1)[-1]
        return FakeResponse(posting_html(identifier))

    collector = LinkedInCollector(
        http_get=get,
        maximum_workers=4,
        minimum_request_interval_seconds=0,
    )
    started = time.perf_counter()
    jobs = list(collector._postings([str(index) for index in range(20)]))
    elapsed = time.perf_counter() - started

    assert len(jobs) == 20
    assert elapsed < 0.8


def test_collector_rejects_unsafe_worker_counts():
    with pytest.raises(ValueError):
        LinkedInCollector(maximum_workers=0)
    with pytest.raises(ValueError):
        LinkedInCollector(request_timeout_seconds=0)
    with pytest.raises(ValueError):
        LinkedInCollector(minimum_request_interval_seconds=-1)
    with pytest.raises(ValueError):
        LinkedInCollector(rate_limit_cooldown_seconds=0)
    with pytest.raises(ValueError):
        LinkedInCollector(rate_limit_retries=-1)


def test_posting_requests_are_paced():
    now = [0.0]
    sleeps = []
    request_times = []

    def sleeper(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def get(url, **_kwargs):
        request_times.append(now[0])
        return FakeResponse(posting_html(url.rsplit("/", 1)[-1]))

    collector = LinkedInCollector(
        http_get=get,
        maximum_workers=1,
        minimum_request_interval_seconds=0.75,
        clock=lambda: now[0],
        sleeper=sleeper,
    )

    assert collector._posting("1") is not None
    assert collector._posting("2") is not None
    assert request_times == [0.0, 0.75]
    assert sleeps == [0.75]


def test_429_waits_for_retry_after_and_retries_once():
    now = [0.0]
    sleeps = []
    calls = []

    def sleeper(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def get(url, **_kwargs):
        calls.append(url)
        if len(calls) == 1:
            return FakeResponse("", status_code=429, headers={"Retry-After": "4"})
        return FakeResponse(posting_html("1"))

    collector = LinkedInCollector(
        http_get=get,
        minimum_request_interval_seconds=0,
        rate_limit_cooldown_seconds=1,
        clock=lambda: now[0],
        sleeper=sleeper,
    )

    assert collector._posting("1").identifier == "1"
    assert len(calls) == 2
    assert sleeps == [4.0]


def test_bundled_chromium_is_discovered_from_frozen_runtime(tmp_path):
    executable = tmp_path / "playwright-browsers" / "chromium-1223" / "chrome-win64" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    assert bundled_chromium_executable(tmp_path) == str(executable)
    assert bundled_chromium_executable(tmp_path / "missing") is None
