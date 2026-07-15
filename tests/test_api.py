from __future__ import annotations

import requests

from UI.api import LocalApiServer, SlidingWindowRateLimiter


def authenticated_session(server):
    session = requests.Session()
    response = session.get(server.url, timeout=5)
    assert response.status_code == 200
    return session, response


def test_api_lists_jobs_with_restrictive_headers_and_no_cors(api_environment):
    server, _ratings, _jobs = api_environment
    session, root = authenticated_session(server)

    assert root.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert root.headers["X-Frame-Options"] == "DENY"
    assert root.headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in root.headers

    response = session.get(server.url + "api/jobs", timeout=5)
    assert response.status_code == 200
    assert response.json()["jobs"][0]["id"] == "linkedin:123"
    assert response.json()["jobs"][0]["description"].startswith("Build reliable")
    assert session.get(server.url + "api/health", timeout=5).json() == {"status": "ok"}
    assert session.get(server.url + "static/styles.css", timeout=5).status_code == 200
    assert server.start() == server.url


def test_api_serves_only_authenticated_validated_translation_catalogs(api_environment):
    server, _ratings, _jobs = api_environment
    assert requests.get(server.url + "api/i18n/pt-BR", timeout=5).status_code == 401
    session, _root = authenticated_session(server)

    response = session.get(server.url + "api/i18n/pt-BR", timeout=5)
    assert response.status_code == 200
    payload = response.json()
    assert payload["locale"] == "pt_BR"
    assert payload["html_language"] == "pt-BR"
    assert payload["messages"]["Refresh jobs"] == "Atualizar vagas"
    assert payload["plurals"]["jobs_loaded"] == {
        "one": "{count} vaga carregada.",
        "other": "{count} vagas carregadas.",
    }
    assert session.get(server.url + "api/i18n/../../.env", timeout=5).status_code == 404
    assert session.get(server.url + "api/i18n/pt-BR?extra=1", timeout=5).status_code == 404


def test_api_rating_lifecycle_accepts_only_server_side_job_data(api_environment):
    server, ratings, _jobs = api_environment
    session, _root = authenticated_session(server)
    endpoint = server.url + "api/jobs/linkedin:123/rating"
    origin = server.url.rstrip("/")

    response = session.put(endpoint, json={"rating": "great"}, headers={"Origin": origin}, timeout=5)
    assert response.status_code == 200
    assert ratings.snapshot().rating_for("linkedin:123") == "great"

    response = session.put(endpoint, json={"rating": "bad"}, headers={"Origin": origin}, timeout=5)
    assert response.status_code == 200
    assert ratings.snapshot().rating_for("linkedin:123") == "bad"

    response = session.delete(endpoint, headers={"Origin": origin}, timeout=5)
    assert response.status_code == 200
    assert ratings.snapshot().rating_for("linkedin:123") is None

    injected = session.put(
        endpoint,
        json={"rating": "great", "description": "browser supplied text"},
        headers={"Origin": origin},
        timeout=5,
    )
    assert injected.status_code == 400
    invalid = session.put(
        endpoint,
        json={"rating": "neutral"},
        headers={"Origin": origin},
        timeout=5,
    )
    assert invalid.status_code == 400


def test_api_rejects_missing_session_wrong_origin_host_method_and_unknown_job(api_environment):
    server, _ratings, _jobs = api_environment
    endpoint = server.url + "api/jobs/linkedin:123/rating"

    assert requests.get(server.url + "api/jobs", timeout=5).status_code == 401
    session, _root = authenticated_session(server)
    assert session.put(endpoint, json={"rating": "bad"}, timeout=5).status_code == 403
    assert session.put(
        endpoint,
        json={"rating": "bad"},
        headers={"Origin": "https://attacker.invalid"},
        timeout=5,
    ).status_code == 403
    assert session.get(server.url + "api/jobs", headers={"Host": "attacker.invalid"}, timeout=5).status_code == 400
    assert session.post(server.url + "api/jobs", timeout=5).status_code == 405
    assert session.put(
        server.url + "api/jobs/linkedin:999/rating",
        json={"rating": "bad"},
        headers={"Origin": server.url.rstrip("/")},
        timeout=5,
    ).status_code == 404


def test_api_rejects_queries_and_non_json_rating_payloads(api_environment):
    server, _ratings, _jobs = api_environment
    session, _root = authenticated_session(server)
    endpoint = server.url + "api/jobs/linkedin:123/rating"
    origin = server.url.rstrip("/")

    assert session.get(server.url + "api/jobs?unexpected=1", timeout=5).status_code == 404
    assert session.put(endpoint, data="rating=bad", headers={"Origin": origin}, timeout=5).status_code == 415


def test_local_rate_limiter_has_a_bounded_window():
    limiter = SlidingWindowRateLimiter(maximum_requests=1, window_seconds=60)
    assert limiter.allow()
    assert not limiter.allow()


def test_api_contains_storage_failures_and_malformed_bodies(api_environment, monkeypatch):
    server, ratings, jobs = api_environment
    session, _root = authenticated_session(server)
    endpoint = server.url + "api/jobs/linkedin:123/rating"
    origin = server.url.rstrip("/")

    monkeypatch.setattr(jobs, "list_jobs", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert session.get(server.url + "api/jobs", timeout=5).status_code == 503
    monkeypatch.setattr(jobs, "list_jobs", lambda: [jobs.job])

    monkeypatch.setattr(jobs, "get", lambda _identifier: (_ for _ in ()).throw(RuntimeError()))
    assert session.put(endpoint, json={"rating": "great"}, headers={"Origin": origin}, timeout=5).status_code == 503
    assert session.delete(endpoint, headers={"Origin": origin}, timeout=5).status_code == 503
    monkeypatch.setattr(jobs, "get", lambda _identifier: jobs.job)

    monkeypatch.setattr(ratings, "rate", lambda *_args: (_ for _ in ()).throw(RuntimeError()))
    assert session.put(endpoint, json={"rating": "great"}, headers={"Origin": origin}, timeout=5).status_code == 503
    monkeypatch.setattr(ratings, "clear", lambda *_args: (_ for _ in ()).throw(RuntimeError()))
    assert session.delete(endpoint, headers={"Origin": origin}, timeout=5).status_code == 503
    monkeypatch.setattr(jobs, "get", lambda _identifier: None)
    assert session.delete(endpoint, headers={"Origin": origin}, timeout=5).status_code == 404

    headers = {"Origin": origin, "Content-Type": "application/json"}
    assert session.put(endpoint, data=b"{x", headers=headers, timeout=5).status_code == 400
    assert session.put(endpoint, data=b"[]", headers=headers, timeout=5).status_code == 400
    assert session.delete(endpoint + "?query=1", headers={"Origin": origin}, timeout=5).status_code == 400
    assert session.delete(endpoint, data=b"x", headers={"Origin": origin}, timeout=5).status_code == 400


def test_api_missing_static_files_and_stop_before_start(api_environment, monkeypatch):
    server, ratings, jobs = api_environment
    session, _root = authenticated_session(server)
    assert session.get(server.url + "static/not-allowed.css", timeout=5).status_code == 404

    static_directory = server._server.context.static_directory
    monkeypatch.setattr(server._server.context, "static_directory", static_directory / "missing")
    assert session.get(server.url + "static/styles.css", timeout=5).status_code == 404
    assert session.get(server.url, timeout=5).status_code == 503

    unopened = LocalApiServer(jobs, ratings, static_directory="UI/static")
    unopened.stop()
