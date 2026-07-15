from __future__ import annotations

import logging
import shutil
import time
from io import BytesIO
from gettext import NullTranslations
from pathlib import Path
from uuid import uuid4

from babel.messages.pofile import read_po

from jobfinder.i18n import BabelPoCatalogRepository, TranslationService, detect_os_locale, normalize_locale
from jobfinder.i18n.extractors import extract_static_html
from jobfinder.i18n.ports import LoadedCatalog


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"


def catalog_ids(path: Path, *, locale: str | None = None) -> set[str]:
    with path.open(encoding="utf-8") as source:
        catalog = read_po(source, locale=locale)
    return {
        message.id if isinstance(message.id, str) else message.id[0]
        for message in catalog
        if message.id
    }


def test_locale_normalization_and_portuguese_family_detection(monkeypatch):
    assert normalize_locale("pt-BR.UTF-8") == "pt_BR"
    assert normalize_locale("pt_PT@euro") == "pt_PT"
    assert normalize_locale("not a locale") is None
    monkeypatch.setattr("jobfinder.i18n.service.system_locale.getlocale", lambda: ("pt_BR", "UTF-8"))
    assert detect_os_locale({}) == "pt_BR"
    assert detect_os_locale({"JOBFINDER_LANG": "en-US"}) == "en_US"


def test_catalog_is_complete_and_preserves_english_fallback():
    pot_ids = catalog_ids(LOCALES / "jobfinder.pot")
    portuguese_ids = catalog_ids(
        LOCALES / "pt_BR" / "LC_MESSAGES" / "jobfinder.po",
        locale="pt_BR",
    )
    translated = pot_ids & portuguese_ids

    assert len(translated) / len(pot_ids) >= 0.90
    assert translated == pot_ids

    english = TranslationService(BabelPoCatalogRepository(LOCALES), requested_locale="en_US")
    assert english.locale == "en"
    assert english.gettext("Home") == "Home"


def test_portuguese_translation_plural_and_language_discovery():
    service = TranslationService(BabelPoCatalogRepository(LOCALES), requested_locale="pt_PT")

    assert service.locale == "pt_BR"
    assert service.html_language == "pt-BR"
    assert service.gettext("Home") == "Início"
    assert service.ngettext("{count} job loaded.", "{count} jobs loaded.", 1) == "1 vaga carregada."
    assert service.ngettext("{count} job loaded.", "{count} jobs loaded.", 3) == "3 vagas carregadas."
    assert service.available_languages() == (("en", "English"), ("pt_BR", "Português (Brasil)"))


def test_missing_key_warns_once_and_falls_back(caplog):
    service = TranslationService(BabelPoCatalogRepository(LOCALES), requested_locale="pt_BR")
    with caplog.at_level(logging.WARNING):
        assert service.gettext("A deliberately missing message") == "A deliberately missing message"
        assert service.gettext("A deliberately missing message") == "A deliberately missing message"

    messages = [record.message for record in caplog.records if "Missing translation key" in record.message]
    assert len(messages) == 1


def test_catalog_only_language_addition_uses_po_fallback():
    runtime_root = ROOT / "ML" / "tests" / "runtime" / f"i18n-{uuid4().hex}"
    target = runtime_root / "es" / "LC_MESSAGES"
    try:
        target.mkdir(parents=True)
        source = LOCALES / "pt_BR" / "LC_MESSAGES" / "jobfinder.po"
        content = source.read_text(encoding="utf-8").replace("Language: pt_BR", "Language: es")
        content = content.replace('msgstr "Português (Brasil)"', 'msgstr "Español"', 1)
        content = content.replace('msgstr "Início"', 'msgstr "Inicio"', 1)
        (target / "jobfinder.po").write_text(content, encoding="utf-8")

        service = TranslationService(BabelPoCatalogRepository(runtime_root), requested_locale="es_MX")

        assert service.locale == "es"
        assert service.gettext("Home") == "Inicio"
        assert ("es", "Español") in service.available_languages()
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)


def test_compiled_catalog_loading_stays_below_fifty_milliseconds():
    durations = []
    for _index in range(5):
        started = time.perf_counter()
        service = TranslationService(BabelPoCatalogRepository(LOCALES), requested_locale="pt_BR")
        assert service.gettext("Home") == "Início"
        durations.append((time.perf_counter() - started) * 1000)
    assert max(durations) < 50


def test_static_html_extractor_handles_bytes_and_translation_attributes():
    source = BytesIO(b'<button data-i18n="Save" data-i18n-aria-label="Save job">Save</button>')
    assert list(extract_static_html(source, (), (), {})) == [
        (1, "gettext", "Save", []),
        (1, "gettext", "Save job", []),
    ]


def test_translation_service_contains_listener_and_formatting_failures(caplog):
    class BrokenFormatTranslations(NullTranslations):
        def gettext(self, message):
            return "{missing}" if message == "format" else message

    class Repository:
        def available_locales(self):
            return ("zz", "bad")

        def load(self, locale):
            if locale == "bad":
                raise LookupError(locale)
            return LoadedCatalog(locale, BrokenFormatTranslations(), frozenset({"format"}), "Test")

    service = TranslationService(Repository(), requested_locale="zz")
    events = []
    unsubscribe = service.subscribe(events.append)
    service.subscribe(lambda _locale: (_ for _ in ()).throw(RuntimeError()))
    with caplog.at_level(logging.WARNING):
        assert service.gettext("format", value="unused") == "format"
        assert service.set_locale("en") == "en"
    assert events == ["en"]
    unsubscribe()
    unsubscribe()
    assert service.set_locale("bad") == "en"
    assert service.fork("zz").locale == "zz"
    assert service.export(("one", "two")) == {"one": "one", "two": "two"}
    assert "Language listener failed safely" in caplog.text
    assert "Translation formatting failed safely" in caplog.text


def test_catalog_repository_caches_and_rejects_missing_catalog():
    repository = BabelPoCatalogRepository(LOCALES)
    first = repository.load("pt_BR")
    assert repository.load("pt_BR") is first
    try:
        repository.load("missing")
    except LookupError as error:
        assert error.args == ("missing",)
    else:
        raise AssertionError("missing catalog must fail safely")
