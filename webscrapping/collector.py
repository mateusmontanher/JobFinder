"""Playwright adapter that preserves LinkedIn card identifier collection."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Iterator
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
        maximum_workers: int = 16,
        request_timeout_seconds: float = 5.0,
        http_get=None,
    ) -> None:
        if maximum_workers < 1 or maximum_workers > 32:
            raise ValueError("maximum_workers must be between 1 and 32")
        if not 0 < request_timeout_seconds <= 30:
            raise ValueError("request_timeout_seconds must be between 0 and 30")
        self.channel = channel
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.maximum_workers = maximum_workers
        self.request_timeout_seconds = request_timeout_seconds
        self._http_get = http_get or requests.get

    def collect(self, url: str) -> list[ScrapedJob]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=self.channel, headless=self.headless)
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
        response = self._http_get(
            endpoint,
            timeout=self.request_timeout_seconds,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "JobFinder/1.0 (local desktop application)",
            },
        )
        response.raise_for_status()
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
