"""Babel extraction support for static HTML translation attributes."""

from __future__ import annotations

from html.parser import HTMLParser


TRANSLATION_ATTRIBUTES = {"data-i18n", "data-i18n-aria-label"}


class _TranslationAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[tuple[int, str]] = []

    def handle_starttag(self, _tag, attrs) -> None:
        line, _column = self.getpos()
        for name, value in attrs:
            if name in TRANSLATION_ATTRIBUTES and value:
                self.messages.append((line, value))


def extract_static_html(fileobj, _keywords, _comment_tags, options):
    del options
    parser = _TranslationAttributeParser()
    content = fileobj.read()
    parser.feed(content.decode("utf-8") if isinstance(content, bytes) else content)
    for line, message in parser.messages:
        yield line, "gettext", message, []
