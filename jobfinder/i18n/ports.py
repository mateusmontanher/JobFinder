"""Ports for loading standard gettext catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from gettext import NullTranslations
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LoadedCatalog:
    locale: str
    translations: NullTranslations
    translated_messages: frozenset[str]
    language_name: str


class CatalogRepository(Protocol):
    def available_locales(self) -> tuple[str, ...]: ...

    def load(self, locale_code: str) -> LoadedCatalog: ...
