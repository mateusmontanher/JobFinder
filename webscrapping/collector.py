"""Playwright adapter that preserves LinkedIn card identifier collection."""

from __future__ import annotations

import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Iterator
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from .domain import ScrapedJob

LOGGER = logging.getLogger(__name__)
CARD_SELECTORS = (
    ".job-search-card",
    "[data-entity-urn*='jobPosting']",
    ".base-card[data-entity-urn]",
)


def bundled_chromium_executable(base_directory: str | Path | None = None) -> str | None:
    """Return the Chromium executable included beside a frozen application, if any."""
    if base_directory is None:
        base_directory = getattr(sys, "_MEIPASS", None)
    if not base_directory:
        return None

    browser_root = Path(base_directory) / "playwright-browsers"
    candidates = sorted(browser_root.glob("chromium-*/chrome-win*/chrome.exe"))
    return str(candidates[-1]) if candidates else None


def normalize_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().casefold())


def is_the_title_in_blacklist(title: str | None) -> bool:
    blocked = (
        "mechanical engineer", "mechanical engineering", "electrical engineer",
        "electrical engineering", "thermal engineer", "thermal engineering",
    )
    normalized = normalize_title(title)
    return any(re.search(rf"\b{re.escape(term)}\b", normalized) for term in blocked)


class LinkedInCollector:
    def __init__(
        self,
        *,
        channel: str = "chrome",
        headless: bool = True,
        timeout_ms: int = 10_000,
        maximum_workers: int = 2,
        request_timeout_seconds: float = 5.0,
        minimum_request_interval_seconds: float = 0.75,
        rate_limit_cooldown_seconds: float = 30.0,
        rate_limit_retries: int = 1,
        http_get=None,
        clock=None,
        sleeper=None,
    ) -> None:
        if maximum_workers < 1 or maximum_workers > 4:
            raise ValueError("maximum_workers must be between 1 and 4")
        if not 0 < request_timeout_seconds <= 30:
            raise ValueError("request_timeout_seconds must be between 0 and 30")
        if minimum_request_interval_seconds < 0:
            raise ValueError("minimum_request_interval_seconds cannot be negative")
        if rate_limit_cooldown_seconds <= 0:
            raise ValueError("rate_limit_cooldown_seconds must be greater than zero")
        if rate_limit_retries < 0:
            raise ValueError("rate_limit_retries cannot be negative")
        self.channel = channel
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.maximum_workers = maximum_workers
        self.request_timeout_seconds = request_timeout_seconds
        self.minimum_request_interval_seconds = minimum_request_interval_seconds
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.rate_limit_retries = rate_limit_retries
        self._http_get = http_get or requests.get
        self._clock = clock or monotonic
        self._sleep = sleeper or sleep
        self._request_lock = Lock()
        self._next_request_at = 0.0

    def collect(self, url: str) -> list[ScrapedJob]:
        with sync_playwright() as playwright:
            bundled_browser = bundled_chromium_executable()
            launch_options = {"headless": self.headless}
            if bundled_browser:
                launch_options["executable_path"] = bundled_browser
            else:
                launch_options["channel"] = self.channel
            browser = playwright.chromium.launch(**launch_options)
            try:
                page = browser.new_page()
                page.set_default_timeout(self.timeout_ms)
                page.goto(url, wait_until="domcontentloaded")
                self._dismiss_consent(page)
                if not self._wait_for_cards(page):
                    LOGGER.error("LinkedIn returned no job cards before the collection timeout")
                    return []
                identifiers = self.card_identifiers(page)
                LOGGER.info("Collected %d LinkedIn card identifiers", len(identifiers))
            finally:
                browser.close()
        return list(self._postings(identifiers))

    @staticmethod
    def card_identifiers(page: Page) -> list[str]:
        """Keep the existing card-code strategy: data-entity-urn -> posting ID."""
        identifiers: list[str] = []
        for selector in CARD_SELECTORS:
            cards = page.locator(selector)
            for index in range(cards.count()):
                urn = cards.nth(index).get_attribute("data-entity-urn")
                if urn:
                    identifier = urn.rsplit(":", 1)[-1]
                    if identifier.isdigit() and identifier not in identifiers:
                        identifiers.append(identifier)
        return identifiers

    def _wait_for_cards(self, page: Page) -> bool:
        try:
            page.locator(", ".join(CARD_SELECTORS)).first.wait_for(
                state="attached", timeout=self.timeout_ms
            )
            return True
        except PlaywrightTimeoutError:
            return False

    @staticmethod
    def _dismiss_consent(page: Page) -> None:
        for label in ("Accept", "Accept cookies", "Agree", "Aceitar", "Concordar"):
            button = page.get_by_role("button", name=label, exact=True)
            if button.count():
                try:
                    button.first.click(timeout=2_000)
                    return
                except PlaywrightTimeoutError:
                    continue

    @staticmethod
    def _page_heading(page: Page) -> str:
        heading = page.locator("h1").first
        return (heading.text_content() or "").strip() if heading.count() else ""

    def _postings(self, identifiers: list[str]) -> Iterator[ScrapedJob]:
        results: dict[int, ScrapedJob] = {}
        with ThreadPoolExecutor(
            max_workers=min(self.maximum_workers, max(1, len(identifiers))),
            thread_name_prefix="jobfinder-posting",
        ) as executor:
            futures = {
                executor.submit(self._posting, identifier): index
                for index, identifier in enumerate(identifiers)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    posting = future.result()
                except Exception as error:
                    LOGGER.error(
                        "Could not collect LinkedIn posting %s (%s)",
                        identifiers[index],
                        type(error).__name__,
                    )
                    continue
                if posting is not None:
                    results[index] = posting

        seen_titles: set[str] = set()
        for index in sorted(results):
            posting = results[index]
            if posting.title in seen_titles or is_the_title_in_blacklist(posting.title):
                continue
            seen_titles.add(posting.title)
            yield posting

    def _posting(self, identifier: str) -> ScrapedJob | None:
        endpoint = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{identifier}"
        for attempt in range(self.rate_limit_retries + 1):
            self._wait_for_request_slot()
            response = self._http_get(
                endpoint,
                timeout=self.request_timeout_seconds,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "JobFinder/1.0 (local desktop application)",
                },
            )
            try:
                response.raise_for_status()
                break
            except requests.HTTPError as error:
                if self._status_code(error) != 429:
                    raise

                cooldown = self._rate_limit_cooldown(error)
                self._defer_requests(cooldown)
                if attempt == self.rate_limit_retries:
                    LOGGER.warning(
                        "LinkedIn rate limited posting %s; fallback retry was exhausted",
                        identifier,
                    )
                    return None
                LOGGER.warning(
                    "LinkedIn rate limited posting %s; retrying after %.1f seconds (%d/%d)",
                    identifier,
                    cooldown,
                    attempt + 1,
                    self.rate_limit_retries,
                )
        soup = BeautifulSoup(response.text, "lxml")
        title_element = soup.select_one(".top-card-layout__title")
        title = normalize_title(title_element.get_text(" ", strip=True) if title_element else "")
        if not title:
            return None
        description = self._soup_text(soup, ".show-more-less-html__markup")
        if not description:
            description = self._soup_text(soup, "section.show-more-less-html")
        if not description:
            LOGGER.error("Skipping posting %s without a description", identifier)
            return None
        company = self._soup_text(soup, ".topcard__org-name-link") or self._soup_text(soup, ".topcard__flavor")
        location = self._soup_text(soup, ".topcard__flavor--bullet")
        logo_element = soup.select_one(".top-card-layout__entity-image")
        logo = ""
        if logo_element is not None:
            logo = (logo_element.get("data-delayed-url") or logo_element.get("src") or "").strip()
        anchor = title_element.find_parent("a", href=True) if title_element is not None else None
        public_url = urljoin(endpoint, anchor.get("href")) if anchor is not None else endpoint
        return ScrapedJob(identifier, title, company, location, description, public_url, logo)

    def _wait_for_request_slot(self) -> None:
        while True:
            with self._request_lock:
                now = self._clock()
                wait_seconds = self._next_request_at - now
                if wait_seconds <= 0:
                    self._next_request_at = now + self.minimum_request_interval_seconds
                    return
            self._sleep(wait_seconds)

    def _defer_requests(self, cooldown: float) -> None:
        with self._request_lock:
            self._next_request_at = max(self._next_request_at, self._clock() + cooldown)

    def _rate_limit_cooldown(self, error: requests.HTTPError) -> float:
        response = getattr(error, "response", None)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        try:
            return max(self.rate_limit_cooldown_seconds, float(retry_after))
        except (TypeError, ValueError):
            return self.rate_limit_cooldown_seconds

    @staticmethod
    def _status_code(error: requests.HTTPError) -> int | None:
        response = getattr(error, "response", None)
        return getattr(response, "status_code", None)

    @staticmethod
    def _soup_text(soup: BeautifulSoup, selector: str) -> str:
        element = soup.select_one(selector)
        return element.get_text(" ", strip=True) if element is not None else ""

    @staticmethod
    def _text(page: Page, selector: str) -> str:
        locator = page.locator(selector).first
        return (locator.text_content() or "").strip() if locator.count() else ""

    @staticmethod
    def _attribute(page: Page, selector: str, name: str) -> str:
        locator = page.locator(selector).first
        return (locator.get_attribute(name) or "").strip() if locator.count() else ""

    @staticmethod
    def _resolve_job_url(page: Page, fallback_url: str) -> str:
        title = page.locator("xpath=/html/body/section/div/div[1]/div/a/h2")
        try:
            title.click(timeout=3_000)
            page.wait_for_timeout(1_500)
            return page.url or fallback_url
        except PlaywrightError as error:
            LOGGER.info("Could not resolve public posting URL; using guest endpoint (%s)", type(error).__name__)
            return fallback_url
