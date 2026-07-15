"""Locale selection and translation use cases shared by every front end."""

from __future__ import annotations

import locale as system_locale
import logging
import os
import re
from gettext import NullTranslations
from threading import RLock
from typing import Callable

from .ports import CatalogRepository, LoadedCatalog


LOGGER = logging.getLogger(__name__)
ENGLISH = "en"
PORTUGUESE_BRAZIL = "pt_BR"
LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:[_-][A-Za-z]{2})?$")


def normalize_locale(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.split(":", 1)[0].split(".", 1)[0].split("@", 1)[0].strip()
    if not LOCALE_PATTERN.fullmatch(candidate):
        return None
    parts = candidate.replace("-", "_").split("_", 1)
    return parts[0].lower() if len(parts) == 1 else f"{parts[0].lower()}_{parts[1].upper()}"


def detect_os_locale(environment: dict[str, str] | None = None) -> str:
    values = os.environ if environment is None else environment
    candidates = [
        values.get("JOBFINDER_LANG"),
        values.get("LANGUAGE"),
        values.get("LC_ALL"),
        values.get("LC_MESSAGES"),
        values.get("LANG"),
    ]
    try:
        candidates.append(system_locale.getlocale()[0])
    except (ValueError, TypeError):
        pass
    return next((normalized for value in candidates if (normalized := normalize_locale(value))), ENGLISH)


class TranslationService:
    """Thread-safe translation facade with deterministic English fallback."""

    def __init__(self, repository: CatalogRepository, *, requested_locale: str | None = None) -> None:
        self._repository = repository
        self._lock = RLock()
        self._listeners: list[Callable[[str], None]] = []
        self._warnings: set[tuple[str, str]] = set()
        self._catalogs: dict[str, LoadedCatalog] = {}
        self._supported = (ENGLISH, *repository.available_locales())
        self._locale = ENGLISH
        self._catalog = self._english_catalog()
        self.set_locale(requested_locale or detect_os_locale(), notify=False)

    @staticmethod
    def _english_catalog() -> LoadedCatalog:
        return LoadedCatalog(ENGLISH, NullTranslations(), frozenset(), "English")

    @property
    def locale(self) -> str:
        return self._locale

    @property
    def html_language(self) -> str:
        return self._locale.replace("_", "-")

    def available_languages(self) -> tuple[tuple[str, str], ...]:
        languages = [(ENGLISH, "English")]
        for locale_code in self._supported:
            if locale_code == ENGLISH:
                continue
            catalog = self._load(locale_code)
            if catalog is not None:
                languages.append((locale_code, catalog.language_name))
        return tuple(languages)

    def resolve_locale(self, requested_locale: str | None) -> str:
        normalized = normalize_locale(requested_locale) or ENGLISH
        lookup = {code.lower(): code for code in self._supported}
        exact = lookup.get(normalized.lower())
        if exact:
            return exact
        language = normalized.split("_", 1)[0]
        same_language = [code for code in self._supported if code.split("_", 1)[0] == language]
        if language == "pt" and PORTUGUESE_BRAZIL in same_language:
            return PORTUGUESE_BRAZIL
        return same_language[0] if same_language else ENGLISH

    def set_locale(self, requested_locale: str | None, *, notify: bool = True) -> str:
        resolved = self.resolve_locale(requested_locale)
        with self._lock:
            catalog = self._english_catalog() if resolved == ENGLISH else self._load(resolved)
            if catalog is None:
                resolved = ENGLISH
                catalog = self._english_catalog()
            changed = resolved != self._locale
            self._locale = resolved
            self._catalog = catalog
            listeners = tuple(self._listeners) if changed and notify else ()
        for listener in listeners:
            try:
                listener(resolved)
            except Exception as error:
                LOGGER.warning("Language listener failed safely (%s)", type(error).__name__)
        return resolved

    def subscribe(self, listener: Callable[[str], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def gettext(self, message: str, **values) -> str:
        with self._lock:
            translated = self._catalog.translations.gettext(message)
            self._warn_if_missing(message)
        return self._format(translated, message, values)

    def ngettext(self, singular: str, plural: str, number: int, **values) -> str:
        with self._lock:
            translated = self._catalog.translations.ngettext(singular, plural, number)
            self._warn_if_missing(singular)
        return self._format(translated, singular if number == 1 else plural, {"count": number, **values})

    def export(self, messages: tuple[str, ...]) -> dict[str, str]:
        return {message: self.gettext(message) for message in messages}

    def fork(self, requested_locale: str | None) -> "TranslationService":
        return TranslationService(self._repository, requested_locale=requested_locale)

    def _load(self, locale_code: str) -> LoadedCatalog | None:
        if locale_code in self._catalogs:
            return self._catalogs[locale_code]
        try:
            catalog = self._repository.load(locale_code)
        except LookupError:
            return None
        self._catalogs[locale_code] = catalog
        return catalog

    def _warn_if_missing(self, message: str) -> None:
        if self._locale == ENGLISH or message in self._catalog.translated_messages:
            return
        warning = (self._locale, message)
        if warning not in self._warnings:
            self._warnings.add(warning)
            LOGGER.warning("Missing translation key for locale %s: %s", self._locale, message)

    @staticmethod
    def _format(translated: str, fallback: str, values: dict) -> str:
        if not values:
            return translated
        try:
            return translated.format(**values)
        except (KeyError, ValueError, IndexError) as error:
            LOGGER.warning("Translation formatting failed safely (%s)", type(error).__name__)
            try:
                return fallback.format(**values)
            except (KeyError, ValueError, IndexError):
                return fallback
