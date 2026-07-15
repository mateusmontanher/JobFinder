"""Hardened loopback-only JSON API and static job-card server."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import re
import secrets
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from jobfinder.feedback import RatedJob, RatingService, SQLiteRatingRepository
from jobfinder.i18n import BabelPoCatalogRepository, TranslationService
from webscrapping.domain import ScrapedJob
from webscrapping.repositories import PostgresJobRepository

LOGGER = logging.getLogger(__name__)


def _(message: str) -> str:
    """Mark a browser message for Babel without translating the API protocol."""
    return message


RATING_ROUTE = re.compile(r"/api/jobs/(?P<identifier>[A-Za-z0-9:_-]{1,128})/rating\Z")
I18N_ROUTE = re.compile(r"/api/i18n/(?P<locale>[A-Za-z]{2,3}(?:[_-][A-Za-z]{2})?)\Z")
SESSION_COOKIE = "jobfinder_session"
MAX_REQUEST_BODY = 1024
STATIC_TYPES = {
    "/static/app.js": "application/javascript; charset=utf-8",
    "/static/styles.css": "text/css; charset=utf-8",
}
BROWSER_MESSAGES = (
    _("JobFinder results"),
    _("Language"),
    _("Local results"),
    _("Refresh jobs"),
    _("Loading jobs…"),
    _("Resume match"),
    _("Open posting"),
    _("Rate this job"),
    _("Like this job"),
    _("Dislike this job"),
    _("Full description"),
    _("Rating saved locally."),
    _("Rating removed."),
    _("The rating could not be saved. Please try again."),
    _("The action could not be completed. See logs/app.log."),
    _("Company not provided"),
    _("Untitled opening"),
    _("Location not provided"),
    _("{percent}% match"),
    _("No description available."),
    _("No jobs are currently available."),
    _("Jobs could not be loaded. Check the local database connection and try again."),
)


class JobReader(Protocol):
    def list_jobs(self) -> list[ScrapedJob]: ...

    def get(self, identifier: str) -> ScrapedJob | None: ...


@dataclass(slots=True)
class SlidingWindowRateLimiter:
    maximum_requests: int = 120
    window_seconds: float = 60.0
    _events: deque[float] = field(default_factory=deque, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._events and self._events[0] <= now - self.window_seconds:
                self._events.popleft()
            if len(self._events) >= self.maximum_requests:
                return False
            self._events.append(now)
            return True


@dataclass(slots=True)
class ApiContext:
    jobs: JobReader
    ratings: RatingService
    translations: TranslationService
    static_directory: Path
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    limiter: SlidingWindowRateLimiter = field(default_factory=SlidingWindowRateLimiter)
    origin: str = ""
    expected_host: str = ""


class SecureLocalHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with loopback validation and bounded concurrency."""

    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], context: ApiContext, *, maximum_workers: int = 8) -> None:
        self.context = context
        self._slots = threading.BoundedSemaphore(maximum_workers)
        super().__init__(address, SecureRequestHandler)
        host, port = self.server_address[:2]
        self.context.expected_host = f"{host}:{port}"
        self.context.origin = f"http://{host}:{port}"

    def verify_request(self, request, client_address) -> bool:
        try:
            return ipaddress.ip_address(client_address[0]).is_loopback
        except ValueError:
            return False

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class SecureRequestHandler(BaseHTTPRequestHandler):
    """Allow only the exact same-origin routes needed by the job-card UI."""

    server_version = "JobFinder"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def context(self) -> ApiContext:
        return self.server.context  # type: ignore[attr-defined]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def handle_one_request(self) -> None:
        self._body_consumed = False
        super().handle_one_request()

    def log_message(self, format: str, *args) -> None:
        del format, args

    def do_GET(self) -> None:
        if not self._allow_request():
            return
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/":
            self._serve_index()
        elif parsed.path in STATIC_TYPES:
            self._serve_static(parsed.path)
        elif parsed.path == "/api/jobs" and self._authenticated():
            self._serve_jobs()
        elif (match := I18N_ROUTE.fullmatch(parsed.path)) and self._authenticated():
            self._serve_translations(match.group("locale"))
        elif parsed.path == "/api/health" and self._authenticated():
            self._json(HTTPStatus.OK, {"status": "ok"})
        else:
            self._error(HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        if not self._allow_request() or not self._authenticated() or not self._same_origin():
            return
        match = RATING_ROUTE.fullmatch(urlsplit(self.path).path)
        if not match or urlsplit(self.path).query:
            self._error(HTTPStatus.NOT_FOUND)
            return
        payload = self._read_json()
        if payload is None:
            return
        if set(payload) != {"rating"} or payload["rating"] not in ("great", "bad"):
            self._error(HTTPStatus.BAD_REQUEST, "invalid rating payload")
            return
        identifier = match.group("identifier")
        try:
            job = self.context.jobs.get(identifier)
        except Exception as error:
            LOGGER.error("Job API lookup failed (%s)", type(error).__name__)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if job is None:
            self._error(HTTPStatus.NOT_FOUND)
            return
        try:
            self.context.ratings.rate(self._rated_job(job), payload["rating"])
        except Exception as error:
            LOGGER.error("Rating write failed for %s (%s)", identifier, type(error).__name__)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        LOGGER.info("Stored %s rating for posting %s", payload["rating"], identifier)
        self._json(HTTPStatus.OK, {"id": identifier, "rating": payload["rating"]})

    def do_DELETE(self) -> None:
        if not self._allow_request() or not self._authenticated() or not self._same_origin():
            return
        match = RATING_ROUTE.fullmatch(urlsplit(self.path).path)
        if not match or urlsplit(self.path).query or self.headers.get("Content-Length") not in (None, "0"):
            self._error(HTTPStatus.BAD_REQUEST)
            return
        identifier = match.group("identifier")
        try:
            job = self.context.jobs.get(identifier)
        except Exception as error:
            LOGGER.error("Job API lookup failed (%s)", type(error).__name__)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if job is None:
            self._error(HTTPStatus.NOT_FOUND)
            return
        try:
            self.context.ratings.clear(identifier)
        except Exception as error:
            LOGGER.error("Rating removal failed for %s (%s)", identifier, type(error).__name__)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        LOGGER.info("Cleared rating for posting %s", identifier)
        self._json(HTTPStatus.OK, {"id": identifier, "rating": None})

    def _method_not_allowed(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, headers={"Allow": "GET, PUT, DELETE"})

    do_CONNECT = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_POST = _method_not_allowed
    do_TRACE = _method_not_allowed

    def handle_expect_100(self) -> bool:
        self._error(HTTPStatus.EXPECTATION_FAILED)
        return False

    def _allow_request(self) -> bool:
        host_values = self.headers.get_all("Host", failobj=[])
        if host_values != [self.context.expected_host]:
            self._error(HTTPStatus.BAD_REQUEST)
            return False
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(HTTPStatus.BAD_REQUEST)
            return False
        if not self.context.limiter.allow():
            self._error(HTTPStatus.TOO_MANY_REQUESTS, headers={"Retry-After": "60"})
            return False
        return True

    def _authenticated(self) -> bool:
        cookie_headers = self.headers.get_all("Cookie", failobj=[])
        if len(cookie_headers) != 1:
            self._error(HTTPStatus.UNAUTHORIZED)
            return False
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_headers[0])
            supplied = cookie[SESSION_COOKIE].value
        except (KeyError, ValueError):
            self._error(HTTPStatus.UNAUTHORIZED)
            return False
        if not hmac.compare_digest(supplied, self.context.session_token):
            self._error(HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _same_origin(self) -> bool:
        origins = self.headers.get_all("Origin", failobj=[])
        if origins != [self.context.origin]:
            self._error(HTTPStatus.FORBIDDEN)
            return False
        return True

    def _serve_index(self) -> None:
        try:
            body = (self.context.static_directory / "index.html").read_bytes()
        except OSError as error:
            LOGGER.error("Browser UI index is unavailable (%s)", type(error).__name__)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        cookie = (
            f"{SESSION_COOKIE}={self.context.session_token}; Path=/; HttpOnly; "
            "SameSite=Strict"
        )
        self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", headers={"Set-Cookie": cookie})

    def _serve_static(self, route: str) -> None:
        filename = route.rsplit("/", 1)[-1]
        try:
            body = (self.context.static_directory / filename).read_bytes()
        except OSError:
            self._error(HTTPStatus.NOT_FOUND)
            return
        self._send(HTTPStatus.OK, body, STATIC_TYPES[route])

    def _serve_jobs(self) -> None:
        try:
            jobs = self.context.jobs.list_jobs()
            statuses = self.context.ratings.snapshot().status_map()
        except Exception as error:
            LOGGER.error("Job API read failed (%s)", type(error).__name__)
            self._error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        records = [
            {
                "id": job.identifier,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description,
                "url": job.url,
                "similarity_percent": round(job.similarity * 100),
                "rating": statuses.get(job.identifier),
            }
            for job in jobs
        ]
        self._json(HTTPStatus.OK, {"jobs": records})

    def _serve_translations(self, requested_locale: str) -> None:
        session = self.context.translations.fork(requested_locale)
        payload = {
            "locale": session.locale,
            "html_language": session.html_language,
            "languages": [
                {"code": code, "name": name}
                for code, name in session.available_languages()
            ],
            "messages": session.export(BROWSER_MESSAGES),
            "plurals": {
                "jobs_loaded": {
                    "one": session.ngettext(
                        "{count} job loaded.",
                        "{count} jobs loaded.",
                        1,
                        count="{count}",
                    ),
                    "other": session.ngettext(
                        "{count} job loaded.",
                        "{count} jobs loaded.",
                        2,
                        count="{count}",
                    ),
                }
            },
        }
        self._json(HTTPStatus.OK, payload)

    @staticmethod
    def _rated_job(job: ScrapedJob) -> RatedJob:
        return RatedJob(job.identifier, job.title, job.description, job.location)

    def _read_json(self) -> dict | None:
        content_types = self.headers.get_all("Content-Type", failobj=[])
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return None
        if len(lengths) != 1:
            self._error(HTTPStatus.LENGTH_REQUIRED)
            return None
        try:
            length = int(lengths[0])
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST)
            return None
        if length < 2 or length > MAX_REQUEST_BODY:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        try:
            raw_body = self.rfile.read(length)
            self._body_consumed = True
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST)
            return None
        return payload

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(
        self,
        status: HTTPStatus,
        message: str | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._discard_small_body()
        self.close_connection = True
        self._json_with_headers(status, {"error": message or status.phrase}, headers or {})

    def _discard_small_body(self) -> None:
        if getattr(self, "_body_consumed", False):
            return
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) != 1:
            return
        try:
            length = int(lengths[0])
        except ValueError:
            return
        if 0 < length <= MAX_REQUEST_BODY:
            self.rfile.read(length)
            self._body_consumed = True

    def _json_with_headers(self, status: HTTPStatus, payload: dict, headers: dict[str, str]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers=headers)

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if self.close_connection:
            self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class LocalApiServer:
    def __init__(
        self,
        jobs: JobReader | None = None,
        ratings: RatingService | None = None,
        *,
        translations: TranslationService | None = None,
        static_directory: str | Path | None = None,
        port: int = 0,
    ) -> None:
        root = Path(static_directory) if static_directory else Path(__file__).with_name("static")
        application_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        context = ApiContext(
            jobs=jobs or PostgresJobRepository(),
            ratings=ratings or RatingService(SQLiteRatingRepository()),
            translations=translations or TranslationService(
                BabelPoCatalogRepository(application_root / "locales")
            ),
            static_directory=root,
        )
        self._server = SecureLocalHTTPServer(("127.0.0.1", port), context)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"{self._server.context.origin}/"

    def start(self) -> str:
        if self._thread and self._thread.is_alive():
            return self.url
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="jobfinder-local-api",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("Local job-card API started on loopback port %d", self._server.server_port)
        return self.url

    def stop(self) -> None:
        if not self._thread:
            self._server.server_close()
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None
        LOGGER.info("Local job-card API stopped")

    def __enter__(self) -> LocalApiServer:
        self.start()
        return self

    def __exit__(self, *_args) -> None:
        self.stop()
