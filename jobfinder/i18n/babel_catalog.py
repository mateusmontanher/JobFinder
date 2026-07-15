"""Babel-backed adapter for portable gettext PO files."""

from __future__ import annotations

import logging
from gettext import GNUTranslations
from io import BytesIO
from pathlib import Path
from threading import RLock

from .ports import LoadedCatalog


LOGGER = logging.getLogger(__name__)
DOMAIN = "jobfinder"
LANGUAGE_NAME_KEY = "__language_name__"


class BabelPoCatalogRepository:
    """Discover PO files and compile them to GNU translations in memory."""

    def __init__(self, locales_directory: str | Path) -> None:
        self._root = Path(locales_directory)
        self._cache: dict[str, LoadedCatalog] = {}
        self._lock = RLock()

    def available_locales(self) -> tuple[str, ...]:
        pattern = f"*/LC_MESSAGES/{DOMAIN}.po"
        return tuple(sorted(path.parents[1].name for path in self._root.glob(pattern)))

    def load(self, locale_code: str) -> LoadedCatalog:
        with self._lock:
            cached = self._cache.get(locale_code)
            if cached is not None:
                return cached
        path = self._root / locale_code / "LC_MESSAGES" / f"{DOMAIN}.po"
        try:
            mo_path = path.with_suffix(".mo")
            if mo_path.is_file():
                with mo_path.open("rb") as binary:
                    translations = GNUTranslations(binary)
                translated = frozenset(
                    key[0] if isinstance(key, tuple) else key
                    for key in translations._catalog  # type: ignore[attr-defined]
                    if key
                )
            else:
                translations, translated = self._compile_po(path, locale_code)
        except (ImportError, OSError, UnicodeError, ValueError) as error:
            LOGGER.warning(
                "Translation catalog could not be loaded for %s (%s)",
                locale_code,
                type(error).__name__,
            )
            raise LookupError(locale_code) from error

        language_name = translations.gettext(LANGUAGE_NAME_KEY)
        if language_name == LANGUAGE_NAME_KEY:
            language_name = locale_code
        loaded = LoadedCatalog(
            locale=locale_code,
            translations=translations,
            translated_messages=translated,
            language_name=language_name,
        )
        with self._lock:
            self._cache[locale_code] = loaded
        return loaded

    @staticmethod
    def _compile_po(path: Path, locale_code: str) -> tuple[GNUTranslations, frozenset[str]]:
        from babel.messages.mofile import write_mo
        from babel.messages.pofile import read_po

        with path.open("r", encoding="utf-8") as source:
            catalog = read_po(
                source,
                locale=locale_code,
                domain=DOMAIN,
                ignore_obsolete=True,
                abort_invalid=True,
            )
        binary = BytesIO()
        write_mo(binary, catalog, use_fuzzy=False)
        binary.seek(0)
        translations = GNUTranslations(binary)
        translated = frozenset(
            message.id if isinstance(message.id, str) else message.id[0]
            for message in catalog
            if message.id
            and message.string
            and "fuzzy" not in message.flags
            and (not isinstance(message.string, tuple) or all(message.string))
        )
        return translations, translated
