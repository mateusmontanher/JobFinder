from __future__ import annotations

import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import UI.main as desktop


ROOT = Path(__file__).resolve().parents[1]


def test_ui_directory_launch_resolves_the_repository_package():
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = (
        "import pathlib, sys; import main; "
        "print(pathlib.Path(sys.modules['UI.api'].__file__).resolve())"
    )

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=ROOT / "UI",
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert Path(result.stdout.strip()) == (ROOT / "UI" / "api.py").resolve()


def test_main_maps_the_window_before_entering_the_event_loop(monkeypatch):
    events: list[str] = []
    log_paths: list[Path] = []

    class FakeApp:
        def update_idletasks(self):
            events.append("update_idletasks")

        def deiconify(self):
            events.append("deiconify")

        def lift(self):
            events.append("lift")

        def mainloop(self):
            events.append("mainloop")

    monkeypatch.setattr(desktop, "JobFinderApp", FakeApp)
    monkeypatch.setattr(desktop, "configure_logging", log_paths.append)

    assert desktop.main() == 0
    assert events == ["update_idletasks", "deiconify", "lift", "mainloop"]
    assert log_paths == [ROOT / "logs" / "app.log"]


def test_postgres_connection_has_a_finite_startup_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_PG_AVAILABLE", True)
    monkeypatch.setattr(desktop.psycopg2, "connect", lambda **kwargs: calls.append(kwargs))

    desktop._pg_connect()

    assert calls[0]["connect_timeout"] == 5


def test_logo_loader_rejects_unapproved_hosts_without_a_request(monkeypatch):
    monkeypatch.setattr(
        desktop.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("an unapproved URL must not be requested"),
    )

    with pytest.raises(ValueError, match="allowed HTTPS LinkedIn host"):
        desktop._download_company_logo("http://127.0.0.1/private.png")


def test_logo_loader_accepts_a_bounded_linkedin_image(monkeypatch):
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color="blue").save(buffer, format="PNG")
    payload = buffer.getvalue()
    calls = []

    class FakeResponse:
        headers = {"Content-Type": "image/png", "Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield payload

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(desktop.requests, "get", fake_get)

    image = desktop._download_company_logo("https://media.licdn.com/logo.png")

    assert image.size == (2, 2)
    assert calls == [(
        "https://media.licdn.com/logo.png",
        {"timeout": (2, 5), "allow_redirects": False, "stream": True},
    )]


def test_ctk_button_keyboard_focus_uses_the_supported_canvas_boundary():
    bindings = {}
    canvas_configuration = []
    button_configuration = []

    class FakeCanvas:
        def configure(self, **kwargs):
            canvas_configuration.append(kwargs)

        def bind(self, event, callback, add):
            bindings[event] = (callback, add)

    class FakeButton:
        _canvas = FakeCanvas()

        def configure(self, **kwargs):
            button_configuration.append(kwargs)

    button = FakeButton()
    desktop._enable_button_keyboard_focus(button)

    assert canvas_configuration == [{"takefocus": True}]
    assert bindings["<FocusIn>"][1] is True
    assert bindings["<FocusOut>"][1] is True

    bindings["<FocusIn>"][0](None)
    bindings["<FocusOut>"][0](None)
    assert button_configuration == [
        {"border_width": 2, "border_color": ("#005FCC", "#7CB9FF")},
        {"border_width": 0},
    ]
