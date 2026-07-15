from __future__ import annotations

import os

import pytest


@pytest.mark.browser
def test_browser_cards_expand_by_keyboard_and_rate_without_reload(api_environment, playwright):
    server, ratings, _jobs = api_environment
    target = os.getenv("JOBFINDER_TEST_BROWSER", "chromium")
    if target == "firefox":
        browser = playwright.firefox.launch(headless=True)
    elif target in ("chrome", "msedge"):
        browser = playwright.chromium.launch(channel=target, headless=True)
    else:
        browser = playwright.chromium.launch(headless=True)

    try:
        page = browser.new_page()
        page.goto(server.url)
        page.get_by_role("heading", name="Senior Python Engineer").wait_for()

        language = page.get_by_label("Language")
        language.select_option("pt_BR")
        page.get_by_role("button", name="Atualizar vagas").wait_for()
        assert page.locator("html").get_attribute("lang") == "pt-BR"
        assert page.get_by_text("1 vaga carregada.").is_visible()
        assert page.get_by_role("group", name="Avaliar esta vaga").is_visible()

        description = page.get_by_role("button", name="Descrição completa")
        description.focus()
        page.keyboard.press("Enter")
        assert description.get_attribute("aria-expanded") == "true"
        assert page.locator(".description-panel").get_attribute("aria-hidden") == "false"

        dislike = page.get_by_role("button", name="Não gostei desta vaga")
        dislike.focus()
        with page.expect_response(lambda response: response.request.method == "PUT" and "/rating" in response.url):
            page.keyboard.press("Space")
        assert dislike.get_attribute("aria-pressed") == "true"
        assert ratings.snapshot().rating_for("linkedin:123") == "bad"
    finally:
        browser.close()
