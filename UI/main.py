import tkinter as tk
import sys
import os
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from concurrent.futures import ThreadPoolExecutor
import logging
from urllib.parse import urlsplit

from jobfinder.feedback import (
    RatedJob,
    RatingService,
    SQLiteRatingRepository,
    stable_job_identifier,
)
from jobfinder.logging_config import configure_logging
from UI.api import LocalApiServer
from webscrapping.main import BrowsingForJobs
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image
from dotenv import load_dotenv
import datetime
import webbrowser
import shutil
import requests
from io import BytesIO
import importlib.util


LOGGER = logging.getLogger(__name__)
ALLOWED_LOGO_HOST = "licdn.com"
MAX_LOGO_BYTES = 2 * 1024 * 1024


def _download_company_logo(url: str) -> Image.Image:
    """Fetch a small LinkedIn-hosted logo without blocking the Tk event loop."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == ALLOWED_LOGO_HOST or hostname.endswith(f".{ALLOWED_LOGO_HOST}")
    ):
        raise ValueError("Logo URL is not an allowed HTTPS LinkedIn host")

    with requests.get(
        url,
        timeout=(2, 5),
        allow_redirects=False,
        stream=True,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("image/"):
            raise ValueError("Logo response is not an image")

        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_LOGO_BYTES:
            raise ValueError("Logo response is too large")

        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            content.extend(chunk)
            if len(content) > MAX_LOGO_BYTES:
                raise ValueError("Logo response is too large")

    image = Image.open(BytesIO(content))
    image.load()
    return image


def _enable_button_keyboard_focus(button: ctk.CTkButton) -> None:
    """Add one visible keyboard tab stop without unsupported CTk kwargs."""
    canvas = button._canvas
    canvas.configure(takefocus=True)
    canvas.bind(
        "<FocusIn>",
        lambda _event: button.configure(
            border_width=2,
            border_color=("#005FCC", "#7CB9FF"),
        ),
        add=True,
    )
    canvas.bind(
        "<FocusOut>",
        lambda _event: button.configure(border_width=0),
        add=True,
    )


def resource_path(*parts):
    base_dir = getattr(sys, "_MEIPASS", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    return os.path.join(base_dir, *parts)


try:
    import psycopg2
    import psycopg2.extras
    _PG_AVAILABLE = True
except ImportError:
    _PG_AVAILABLE = False

env_file = resource_path(".env")
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
#  Runtime DB credentials (overridden by the in-app form when the user connects)
# ─────────────────────────────────────────────────────────────────────────────
try:
    _DB_CREDENTIALS: dict = {
        "host":     os.getenv("DB_HOST",     "localhost"),
        "port":     int(os.getenv("DB_PORT", 5432)),
        "dbname":   os.getenv("DB_NAME",     ""),
        "user":     os.getenv("DB_USER",     ""),
        "password": os.getenv("DB_PASSWORD", ""),
    }
except Exception as e:
    print(f"Error loading DB credentials from environment: {e}")
    _DB_CREDENTIALS = {
        "host":     "localhost",
        "port":     5432,
        "dbname":   "",
        "user":     "",
        "password": "",
    }

# ─────────────────────────────────────────────────────────────────────────────
#  PostgreSQL helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pg_connect():
    if not _PG_AVAILABLE:
        raise RuntimeError("psycopg2 is not installed.  Run: pip install psycopg2-binary")
    return psycopg2.connect(connect_timeout=5, **_DB_CREDENTIALS)


def _pg_ensure_tables():
    """Create favorites and curriculums tables if they do not exist."""
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS favorites_jobs (
                    id                SERIAL PRIMARY KEY,
                    company_name      TEXT NOT NULL,
                    job_title         TEXT NOT NULL,
                    salary            TEXT,
                    location          TEXT,
                    deadline          TEXT,
                    link_url          TEXT,
                    match_pct         INTEGER,
                    company_logo_path TEXT,
                    source_id         TEXT,
                    description       TEXT,
                    created_at        TIMESTAMPTZ DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS curriculum (
                    id               SERIAL PRIMARY KEY,
                    filename         TEXT NOT NULL,
                    file_data        BYTEA NOT NULL,
                    uploaded_at      TIMESTAMPTZ DEFAULT now(),
                    raw_text         TEXT,
                    candidate_name   TEXT,
                    email            TEXT,
                    phone            TEXT,
                    location         TEXT,
                    keywords         TEXT[],
                    hard_skills      TEXT[],
                    soft_skills      TEXT[],
                    languages        TEXT[],
                    years_experience INTEGER,
                    seniority_level  TEXT,
                    education_level  TEXT,
                    education_field  TEXT,
                    ml_processed     BOOLEAN DEFAULT false,
                    ml_processed_at  TIMESTAMPTZ,
                    ml_model_version TEXT,
                    embedding        VECTOR(1536)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id SERIAL PRIMARY KEY,
                    source_id TEXT,
                    company_name TEXT,
                    job_title TEXT,
                    job_type TEXT,
                    description TEXT,
                    salary_min NUMERIC,
                    salary_max NUMERIC,
                    locate VARCHAR(255),
                    deadline DATE,
                    url TEXT,
                    company_logo_path TEXT,
                    keywords TEXT[],
                    published DATE,
                    similarity NUMERIC(5,2)
                );

                ALTER TABLE favorites_jobs ADD COLUMN IF NOT EXISTS source_id TEXT;
                ALTER TABLE favorites_jobs ADD COLUMN IF NOT EXISTS description TEXT;
                ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_id TEXT;
            """)
        conn.commit()


# ── Favorites CRUD ────────────────────────────────────────────────────────────

def pg_add_favorite(job: dict):
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO favorites_jobs
                    (company_name, job_title, salary, location,
                     deadline, link_url, match_pct, company_logo_path,
                     source_id, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (job.get("company_name"), job.get("job_title"),
                  job.get("salary"), job.get("location"),
                  job.get("deadline"), job.get("link_url"),
                  job.get("match_pct"), job.get("company_logo_path"),
                  stable_job_identifier(
                      job.get("id"),
                      title=job.get("job_title", ""),
                      description=job.get("description", ""),
                      location=job.get("location", ""),
                  ),
                  job.get("description", "")))
        conn.commit()


def pg_remove_favorite(company_name: str, job_title: str):
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM favorites_jobs WHERE company_name=%s AND job_title=%s",
                        (company_name, job_title))
        conn.commit()


def pg_load_favorites() -> list[dict]:
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT company_name, job_title, salary, location,
                       deadline, link_url, match_pct, company_logo_path,
                       source_id AS id, description
                FROM favorites_jobs ORDER BY created_at DESC
            """)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def pg_is_favorite(company_name: str, job_title: str) -> bool:
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM favorites_jobs WHERE company_name=%s AND job_title=%s LIMIT 1",
                        (company_name, job_title))
            return cur.fetchone() is not None


# ── Curriculums CRUD ──────────────────────────────────────────────────────────

def pg_upload_curriculum(file_path: str) -> int:
    """Insert binary file into DB; returns new row id."""
    _pg_ensure_tables()
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        data = fh.read()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO curriculum (filename, file_data) VALUES (%s,%s) RETURNING id",
                (filename, psycopg2.Binary(data))
            )
            new_id = cur.fetchone()[0]
        conn.commit()
    return new_id


def pg_load_curriculums() -> list[dict]:
    """Return id + filename (no binary) for all stored curriculums."""
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, filename, uploaded_at FROM curriculum ORDER BY uploaded_at DESC")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def pg_download_curriculum(row_id: int, dest_path: str):
    """Write curriculum binary from DB to dest_path on disk."""
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT file_data FROM curriculum WHERE id=%s", (row_id,))
            row = cur.fetchone()
    if row is None:
        raise ValueError(f"No curriculum found with id={row_id}")
    with open(dest_path, "wb") as fh:
        fh.write(bytes(row[0]))


def pg_update_curriculum(row_id: int, new_file_path: str):
    """Replace the binary of an existing curriculum row."""
    _pg_ensure_tables()
    filename = os.path.basename(new_file_path)
    with open(new_file_path, "rb") as fh:
        data = fh.read()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE curriculum SET filename=%s, file_data=%s, uploaded_at=now() WHERE id=%s",
                (filename, psycopg2.Binary(data), row_id)
            )
        conn.commit()


def pg_delete_curriculum(row_id: int):
    _pg_ensure_tables()
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM curriculum WHERE id=%s", (row_id,))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  Main application
# ─────────────────────────────────────────────────────────────────────────────

def _call_browsing_for_jobs():
    try:
        return BrowsingForJobs()
    except Exception as error:
        LOGGER.error("Job search failed safely (%s)", type(error).__name__)
        raise

class JobFinderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("JobFinder")
        self.geometry("900x600")
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jobfinder-ui")
        self._image_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jobfinder-images")
        self._rating_service: RatingService | None = None
        self._local_api: LocalApiServer | None = None

        try:
            self._rating_service = RatingService(SQLiteRatingRepository())
        except Exception as error:
            LOGGER.error("Local feedback database is unavailable (%s)", type(error).__name__)

        if self._rating_service is not None:
            try:
                self._local_api = LocalApiServer(ratings=self._rating_service)
                self._local_api.start()
            except Exception as error:
                LOGGER.error("Local browser API could not start (%s)", type(error).__name__)
                self._local_api = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Sidebar animation state
        self._sidebar_visible = True
        self._sidebar_target_width = 200
        self._sidebar_current_width = 200

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.side_bar = ctk.CTkFrame(self, width=200)
        self.side_bar.grid(row=0, column=0, sticky="nsew", padx=(10, 0))

        self.main_cointeiner = ctk.CTkFrame(self, width=400)
        self.main_cointeiner.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        self.Sidebar()
        self.MainConteiner()

    def _on_close(self):
        if self._local_api is not None:
            try:
                self._local_api.stop()
            except Exception as error:
                LOGGER.error("Local browser API shutdown failed (%s)", type(error).__name__)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._image_executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()

    def report_callback_exception(self, exception_type, exception, traceback):
        del exception, traceback
        LOGGER.error("Unhandled UI callback was contained (%s)", exception_type.__name__)
        messagebox.showerror("Application error", "The action could not be completed. See logs/app.log.")

    def _open_browser_view(self):
        if self._local_api is None:
            messagebox.showwarning("Browser view unavailable", "The local browser service could not be started.")
            return
        webbrowser.open(self._local_api.url)

    def _rating_statuses(self) -> dict[str, str]:
        if self._rating_service is None:
            return {}
        try:
            return self._rating_service.snapshot().status_map()
        except Exception as error:
            LOGGER.error("Could not load rating status (%s)", type(error).__name__)
            return {}

    def _persist_rating_async(self, job: RatedJob, rating: str | None, callback):
        if self._rating_service is None:
            callback(False)
            return

        def persist():
            if rating is None:
                self._rating_service.clear(job.identifier)
            else:
                self._rating_service.rate(job, rating)

        future = self._executor.submit(persist)

        def publish_when_ready():
            if not future.done():
                self.after(50, publish_when_ready)
                return
            try:
                error = future.exception()
            except Exception as callback_error:
                error = callback_error
            if error is not None:
                LOGGER.error("Asynchronous rating failed for %s (%s)", job.identifier, type(error).__name__)
            callback(error is None)

        self.after(50, publish_when_ready)

    # ─────────────────────────────────────────────────────────────────────────
    #  SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────

    def Sidebar(self):

        # ── Hamburger (close sidebar) ─────────────────────────────────────
        try:
            _menu_img = ctk.CTkImage(
                light_image=Image.open(resource_path("UI", "menu.png")),
                dark_image=Image.open(resource_path("UI", "menu.png")),
                size=(24, 24)
            )
            sidebar_button = ctk.CTkButton(
                self.side_bar, text="", image=_menu_img,
                width=40, height=40, corner_radius=10,
                fg_color="transparent", hover_color=("gray85", "gray20"),
                command=self._toggle_sidebar
            )
        except Exception:
            sidebar_button = ctk.CTkButton(
                self.side_bar, text="☰",
                width=40, height=40, corner_radius=10,
                fg_color="transparent", hover_color=("gray85", "gray20"),
                command=self._toggle_sidebar
            )
        sidebar_button.pack(anchor="w", padx=15, pady=(15, 25))

        # ── User section ──────────────────────────────────────────────────
        try:
            user_img = ctk.CTkImage(
                light_image=Image.open(resource_path("UI", "user.png")),
                dark_image=Image.open(resource_path("UI", "user.png")),
                size=(72, 72)
            )
            ctk.CTkLabel(self.side_bar, text="", image=user_img).pack(pady=(0, 10))
        except Exception:
            ctk.CTkLabel(self.side_bar, text="👤", font=("Segoe UI", 40)).pack(pady=(0, 10))

        ctk.CTkLabel(
            self.side_bar,
            text=os.getenv("USERNAME", "User"),
            font=("Segoe UI", 22, "bold")
        ).pack()

        ctk.CTkLabel(
            self.side_bar,
            text=os.getenv("ROLE", ""),
            font=("Segoe UI", 12),
            text_color=("gray45", "gray65")
        ).pack(pady=(0, 20))

        # ── Divider ───────────────────────────────────────────────────────
        ctk.CTkFrame(self.side_bar, height=2, fg_color=("gray80", "gray25")
                     ).pack(fill="x", padx=20, pady=(0, 20))

        # ── Navigation ────────────────────────────────────────────────────
        ctk.CTkButton(
            self.side_bar, text="🏠  Home",
            anchor="w", height=40, corner_radius=10,
            command=self._show_home
        ).pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(
            self.side_bar, text="★  Favorites",
            anchor="w", height=40, corner_radius=10,
            command=self._show_favorites
        ).pack(fill="x", padx=15, pady=5)

        ctk.CTkButton(
            self.side_bar, text="📚  Curriculums",
            anchor="w", height=40, corner_radius=10,
            command=self._show_curriculum
        ).pack(fill="x", padx=15, pady=5)

        # ── Bottom ────────────────────────────────────────────────────────
        ctk.CTkFrame(self.side_bar, height=2, fg_color=("gray80", "gray25")
                     ).pack(side="bottom", fill="x", padx=20, pady=(10, 10))

        ctk.CTkLabel(
            self.side_bar,
            text=f"Developed by {os.getenv('ME', 'Me')}",
            font=("Segoe UI", 11),
            text_color=("gray50", "gray60")
        ).pack(side="bottom", pady=(0, 15))

    # ── Sidebar animation ─────────────────────────────────────────────────────

    def _toggle_sidebar(self):
        self._sidebar_visible = not self._sidebar_visible
        self._sidebar_target_width = 200 if self._sidebar_visible else 0
        self._animate_sidebar()

    def _animate_sidebar(self):
        current = self._sidebar_current_width
        target  = self._sidebar_target_width

        if current == target:
            if target == 0:
                self.side_bar.grid_remove()
                self._show_reopen_button()
            return

        new_w = min(current + 20, target) if current < target else max(current - 20, target)
        self._sidebar_current_width = new_w

        if self._sidebar_visible:
            self.side_bar.grid()
            self._hide_reopen_button()

        self.side_bar.configure(width=new_w)
        self.after(10, self._animate_sidebar)

    def _show_reopen_button(self):
        """Small floating button that reopens the sidebar when it is hidden."""
        if not hasattr(self, "_reopen_btn") or not self._reopen_btn.winfo_exists():
            self._reopen_btn = ctk.CTkButton(
                self.main_cointeiner,
                text="☰",
                width=36, height=36,
                corner_radius=10,
                font=("Segoe UI", 18),
                fg_color=("gray85", "gray20"),
                hover_color=("gray75", "gray30"),
                command=self._toggle_sidebar,
            )
        self._reopen_btn.place(x=10, y=10)

    def _hide_reopen_button(self):
        if hasattr(self, "_reopen_btn") and self._reopen_btn.winfo_exists():
            self._reopen_btn.place_forget()

    # ─────────────────────────────────────────────────────────────────────────
    #  VIEW ROUTING
    # ─────────────────────────────────────────────────────────────────────────

    def _clear_main(self):
        for w in self.main_cointeiner.winfo_children():
            # Never destroy the reopen button
            if hasattr(self, "_reopen_btn") and w is self._reopen_btn:
                continue
            w.destroy()

    def _show_home(self):
        self._clear_main()
        self.MainConteiner()

    def _show_favorites(self):
        self._clear_main()
        self._build_favorites_view()

    def _show_curriculum(self):
        self._clear_main()
        self._build_curriculum_view()

    # ─────────────────────────────────────────────────────────────────────────
    #  FAVORITES VIEW  (with DB connection form)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_favorites_view(self):
        self.main_cointeiner.grid_columnconfigure(0, weight=1)
        self.main_cointeiner.grid_rowconfigure(2, weight=1)

        # ── Title ─────────────────────────────────────────────────────────
        ctk.CTkLabel(
            self.main_cointeiner,
            text="★  Favorites",
            font=("Segoe UI", 26, "bold"),
            anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(18, 0))

        # ── DB Connection form (vertical layout) ──────────────────────────
        form_frame = ctk.CTkFrame(self.main_cointeiner, corner_radius=12,
                                  border_width=1, border_color=("gray80", "gray30"))
        form_frame.grid(row=1, column=0, sticky="ew", padx=24, pady=(10, 6))
        form_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form_frame, text="Database connection",
                     font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        fields = [
            ("Host",     "host"),
            ("Port",     "port"),
            ("DB Name",  "dbname"),
            ("User",     "user"),
            ("Password", "password"),
        ]
        entries: dict[str, ctk.CTkEntry] = {}

        for row_idx, (label, key) in enumerate(fields, start=1):
            ctk.CTkLabel(form_frame, text=label,
                         font=("Segoe UI", 11), anchor="w").grid(
                row=row_idx, column=0, sticky="w", padx=(14, 8), pady=(0, 8))
            show = "*" if key == "password" else ""
            ent = ctk.CTkEntry(form_frame, show=show,
                               placeholder_text=str(_DB_CREDENTIALS.get(key, "")))
            ent.insert(0, str(_DB_CREDENTIALS.get(key, "")))
            ent.grid(row=row_idx, column=1, sticky="ew", padx=(0, 14), pady=(0, 8))
            entries[key] = ent

        status_lbl = ctk.CTkLabel(form_frame, text="", font=("Segoe UI", 11), anchor="w")
        status_lbl.grid(row=len(fields) + 1, column=0, columnspan=2,
                        sticky="w", padx=14, pady=(0, 6))

        scroll_holder: dict = {"frame": None}

        def _connect_and_load():
            _DB_CREDENTIALS["host"]     = entries["host"].get().strip()    or _DB_CREDENTIALS["host"]
            _DB_CREDENTIALS["port"]     = int(entries["port"].get().strip() or _DB_CREDENTIALS["port"])
            _DB_CREDENTIALS["dbname"]   = entries["dbname"].get().strip()  or _DB_CREDENTIALS["dbname"]
            _DB_CREDENTIALS["user"]     = entries["user"].get().strip()    or _DB_CREDENTIALS["user"]
            _DB_CREDENTIALS["password"] = entries["password"].get().strip()

            status_lbl.configure(text="Connecting…", text_color=("gray45", "gray65"))
            self.update_idletasks()

            try:
                favorites = pg_load_favorites()
                status_lbl.configure(text=f"✅  Connected — {len(favorites)} favorite(s) loaded.",
                                     text_color=("#2e7d32", "#81c784"))
            except Exception as exc:
                status_lbl.configure(text=f"❌  {exc}", text_color=("red", "#ff6b6b"))
                return

            # Rebuild scroll area
            if scroll_holder["frame"] and scroll_holder["frame"].winfo_exists():
                scroll_holder["frame"].destroy()

            scroll = ctk.CTkScrollableFrame(
                self.main_cointeiner,
                fg_color="transparent",
                scrollbar_button_color=("gray75", "gray35"),
                scrollbar_button_hover_color=("gray60", "gray50")
            )
            scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
            scroll.grid_columnconfigure(0, weight=1)
            scroll_holder["frame"] = scroll

            if not favorites:
                ctk.CTkLabel(scroll,
                             text="No favorites yet.\nStar a job on the home screen to save it here.",
                             font=("Segoe UI", 14),
                             text_color=("gray45", "gray65")
                             ).grid(row=0, column=0, pady=40)
                return

            rating_statuses = self._rating_statuses()
            for idx, job in enumerate(favorites):
                job["id"] = stable_job_identifier(
                    job.get("id"),
                    title=job.get("job_title", ""),
                    description=job.get("description", ""),
                    location=job.get("location", ""),
                )
                job["rating"] = rating_statuses.get(job["id"])
                self._create_job_card(scroll, idx, job, in_favorites=True)

        ctk.CTkButton(
            form_frame, text="Connect & Load", height=36,
            corner_radius=8, font=("Segoe UI", 12, "bold"),
            command=_connect_and_load
        ).grid(row=len(fields) + 2, column=0, columnspan=2,
               sticky="ew", padx=14, pady=(4, 14))

    # ─────────────────────────────────────────────────────────────────────────
    #  CURRICULUM VIEW  (full CRUD)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_curriculum_view(self):
        self.main_cointeiner.grid_columnconfigure(0, weight=1)
        self.main_cointeiner.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self.main_cointeiner,
            text="📚  Curriculums",
            font=("Segoe UI", 26, "bold"),
            anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(18, 0))

        # ── Split: left = upload panel, right = list ──────────────────────
        body = ctk.CTkFrame(self.main_cointeiner, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=10)
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # ── Upload panel ──────────────────────────────────────────────────
        upload_panel = ctk.CTkFrame(body, corner_radius=14,
                                    border_width=1, border_color=("gray80", "gray30"),
                                    width=240)
        upload_panel.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        upload_panel.grid_propagate(False)

        ctk.CTkLabel(
            upload_panel,
            text="Submit your curriculum below",
            font=("Segoe UI", 15, "bold"),
            wraplength=200
        ).pack(padx=16, pady=(20, 6))

        ctk.CTkLabel(
            upload_panel,
            text="We only allow PDF or Word files (.docx)",
            font=("Segoe UI", 11),
            text_color=("gray45", "gray65"),
            wraplength=200
        ).pack(padx=16, pady=(0, 20))

        upload_status = ctk.CTkLabel(upload_panel, text="",
                                     font=("Segoe UI", 11), wraplength=200)
        upload_status.pack(padx=16)

        # Refresh callback – filled in below once list_scroll exists
        _refresh_ref: dict = {"fn": lambda: None}

        def _do_upload():
            fp = filedialog.askopenfilename(
                title="Select your curriculum",
                filetypes=[("Allowed files", "*.pdf *.docx"),
                           ("PDF", "*.pdf"), ("Word", "*.docx")]
            )
            if not fp:
                return
            if os.path.splitext(fp)[1].lower() not in (".pdf", ".docx"):
                upload_status.configure(
                    text="❌  Invalid file type.", text_color=("red", "#ff6b6b"))
                return
            try:
                pg_upload_curriculum(fp)
                upload_status.configure(
                    text=f"✅  '{os.path.basename(fp)}' uploaded.",
                    text_color=("#2e7d32", "#81c784"))
                _refresh_ref["fn"]()
            except Exception as exc:
                upload_status.configure(
                    text=f"❌  {exc}", text_color=("red", "#ff6b6b"))

        ctk.CTkButton(
            upload_panel, text="📂  Upload File",
            width=180, height=44, corner_radius=12,
            font=("Segoe UI", 14, "bold"),
            command=_do_upload
        ).pack(pady=(12, 20))

        # ── Curriculum list ───────────────────────────────────────────────
        list_outer = ctk.CTkFrame(body, corner_radius=14,
                                  border_width=1, border_color=("gray80", "gray30"))
        list_outer.grid(row=0, column=1, sticky="nsew")
        list_outer.grid_columnconfigure(0, weight=1)
        list_outer.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(list_outer, text="Stored Curriculums",
                     font=("Segoe UI", 14, "bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        list_scroll = ctk.CTkScrollableFrame(
            list_outer, fg_color="transparent",
            scrollbar_button_color=("gray75", "gray35"),
            scrollbar_button_hover_color=("gray60", "gray50")
        )
        list_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        list_scroll.grid_columnconfigure(0, weight=1)

        # ── Build / refresh list ──────────────────────────────────────────
        def _refresh_list():
            for w in list_scroll.winfo_children():
                w.destroy()

            try:
                rows = pg_load_curriculums()
            except Exception as exc:
                ctk.CTkLabel(list_scroll,
                             text=f"⚠️  DB error:\n{exc}",
                             font=("Segoe UI", 12),
                             text_color=("red", "#ff6b6b"),
                             wraplength=300
                             ).grid(row=0, column=0, pady=20)
                return

            if not rows:
                ctk.CTkLabel(list_scroll,
                             text="No curriculums uploaded yet.",
                             font=("Segoe UI", 13),
                             text_color=("gray45", "gray65")
                             ).grid(row=0, column=0, pady=20)
                return

            for i, row in enumerate(rows):
                _build_curriculum_row(list_scroll, i, row)

        def _build_curriculum_row(parent, row_idx: int, row: dict):
            rid      = row["id"]
            fname    = row["filename"]
            uploaded = row.get("uploaded_at", "")
            if hasattr(uploaded, "strftime"):
                uploaded = uploaded.strftime("%d/%m/%Y %H:%M")

            card = ctk.CTkFrame(parent, corner_radius=10,
                                border_width=1, border_color=("gray82", "gray28"))
            card.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=5)
            card.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(card, text=fname,
                         font=("Segoe UI", 13, "bold"), anchor="w"
                         ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
            ctk.CTkLabel(card, text=f"Uploaded: {uploaded}",
                         font=("Segoe UI", 10),
                         text_color=("gray45", "gray65"), anchor="w"
                         ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))

            btn_row = ctk.CTkFrame(card, fg_color="transparent")
            btn_row.grid(row=0, column=1, rowspan=2, padx=(6, 12), pady=6)

            # ── Download ─────────────────────────────────────────────────
            def _download(_rid=rid, _fname=fname):
                dest = filedialog.asksaveasfilename(
                    defaultextension=os.path.splitext(_fname)[1],
                    initialfile=_fname,
                    filetypes=[("All files", "*.*")]
                )
                if not dest:
                    return
                try:
                    pg_download_curriculum(_rid, dest)
                    messagebox.showinfo("Download", f"Saved to:\n{dest}")
                except Exception as exc:
                    messagebox.showerror("Error", str(exc))

            ctk.CTkButton(
                btn_row, text="⬇", width=34, height=34,
                corner_radius=8, font=("Segoe UI", 16),
                fg_color="transparent",
                hover_color=("gray88", "gray22"),
                command=_download
            ).pack(side="left", padx=3)

            # ── Update (replace file) ─────────────────────────────────────
            def _update(_rid=rid):
                fp = filedialog.askopenfilename(
                    title="Select replacement file",
                    filetypes=[("Allowed files", "*.pdf *.docx"),
                               ("PDF", "*.pdf"), ("Word", "*.docx")]
                )
                if not fp:
                    return
                if os.path.splitext(fp)[1].lower() not in (".pdf", ".docx"):
                    messagebox.showerror("Invalid file", "Only PDF or .docx allowed.")
                    return
                try:
                    pg_update_curriculum(_rid, fp)
                    messagebox.showinfo("Updated", "Curriculum updated successfully.")
                    _refresh_list()
                except Exception as exc:
                    messagebox.showerror("Error", str(exc))

            ctk.CTkButton(
                btn_row, text="✏", width=34, height=34,
                corner_radius=8, font=("Segoe UI", 16),
                fg_color="transparent",
                hover_color=("gray88", "gray22"),
                command=_update
            ).pack(side="left", padx=3)

            # ── Delete ────────────────────────────────────────────────────
            def _delete(_rid=rid, _fname=fname):
                if not messagebox.askyesno(
                        "Delete", f"Permanently delete '{_fname}'?"):
                    return
                try:
                    pg_delete_curriculum(_rid)
                    _refresh_list()
                except Exception as exc:
                    messagebox.showerror("Error", str(exc))

            ctk.CTkButton(
                btn_row, text="🗑", width=34, height=34,
                corner_radius=8, font=("Segoe UI", 16),
                fg_color="transparent",
                text_color=("red", "#ff6b6b"),
                hover_color=("gray88", "gray22"),
                command=_delete
            ).pack(side="left", padx=3)

        # Wire refresh callback so upload panel can trigger it
        _refresh_ref["fn"] = _refresh_list
        _refresh_list()

    # ─────────────────────────────────────────────────────────────────────────
    #  MAIN CONTAINER  (home – job carousel)
    # ─────────────────────────────────────────────────────────────────────────

    def MainConteiner(self):
        def get_greeting() -> str:
            h = datetime.datetime.now().hour
            return "Good Morning" if h < 13 else "Good Afternoon" if h < 18 else "Good Night"
 
        def toggle_theme():
            mode = ctk.get_appearance_mode()
            new  = "Light" if mode == "Dark" else "Dark"
            ctk.set_appearance_mode(new)
            theme_btn.configure(text="☀️  Light Mode" if new == "Light" else "🌙  Dark Mode")
 
        # Reset stale row weights left by other views
        for _i in range(5):
            self.main_cointeiner.grid_rowconfigure(_i, weight=0)
        self.main_cointeiner.grid_columnconfigure(0, weight=1)
        self.main_cointeiner.grid_rowconfigure(3, weight=1)
 
        # ── Top bar ───────────────────────────────────────────────────────
        top_bar = ctk.CTkFrame(self.main_cointeiner, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        top_bar.grid_columnconfigure(0, weight=1)
 
        ctk.CTkLabel(
            top_bar,
            text=f"Hey, {get_greeting()} 👋",
            font=("Segoe UI", 26, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
 
        theme_btn = ctk.CTkButton(
            top_bar, text="🌙  Dark Mode",
            width=130, height=34, corner_radius=17,
            font=("Segoe UI", 13), command=toggle_theme,
        )
        theme_btn.grid(row=0, column=2, sticky="e")

        browser_btn = ctk.CTkButton(
            top_bar,
            text="Open browser view",
            width=150,
            height=34,
            corner_radius=17,
            font=("Segoe UI", 13),
            command=self._open_browser_view,
            state="normal" if self._local_api is not None else "disabled",
        )
        browser_btn.grid(row=0, column=1, sticky="e", padx=(8, 8))
 
        # ── Subtitle ──────────────────────────────────────────────────────
        ctk.CTkLabel(
            self.main_cointeiner,
            text="Look what we've found:",
            font=("Segoe UI", 15),
            text_color=("gray45", "gray65"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(6, 4))
 
        # ── Scrollable carousel ───────────────────────────────────────────
        scroll_frame = ctk.CTkScrollableFrame(
            self.main_cointeiner,
            fg_color="transparent",
            scrollbar_button_color=("gray75", "gray35"),
            scrollbar_button_hover_color=("gray60", "gray50"),
        )
        scroll_frame.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 16))
        scroll_frame.grid_columnconfigure(0, weight=1)
 
        sample_jobs: list[dict] = []
 
        # ── Render cards ──────────────────────────────────────────────────
        def render_jobs():
            for widget in scroll_frame.winfo_children():
                widget.destroy()
            if not sample_jobs:
                ctk.CTkLabel(
                    scroll_frame,
                    text="No job openings found.\nClick «Search for new jobs» to fetch the latest listings.",
                    font=("Segoe UI", 14),
                    text_color=("gray45", "gray65"),
                    justify="center",
                ).grid(row=0, column=0, pady=60)
                return
            for idx, job in enumerate(sample_jobs):
                self._create_job_card(scroll_frame, idx, job, in_favorites=False)
 
        # ── Fetch from DB ─────────────────────────────────────────────────
        def fetch_for_jobs(on_complete=None):
            for widget in scroll_frame.winfo_children():
                widget.destroy()
            ctk.CTkLabel(
                scroll_frame,
                text="Loading saved job openings...",
                font=("Segoe UI", 14),
                text_color=("gray45", "gray65"),
            ).grid(row=0, column=0, pady=60)

            def load_jobs() -> list[dict]:
                loaded_jobs: list[dict] = []
                try:
                    with _pg_connect() as conn:
                        with conn.cursor() as cur:
                            cur.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_id TEXT")
                            cur.execute("""
                                SELECT
                                    source_id,
                                    company_logo_path,
                                    company_name,
                                    job_title,
                                    salary_min,
                                    locate,
                                    deadline,
                                    url,
                                    similarity,
                                    description
                                FROM jobs
                                ORDER BY similarity DESC NULLS LAST
                            """)
                            rows = cur.fetchall()

                    rating_statuses = self._rating_statuses()
                    for (source_id, company_logo_path, company_name, job_title,
                         salary_min, locate, deadline, url,
                         similarity, description) in rows:
                        job_id = stable_job_identifier(
                            source_id,
                            title=job_title or "",
                            description=description or "",
                            location=locate or "",
                        )
                        loaded_jobs.append({
                            "id": job_id,
                            "company_logo_path": company_logo_path or "",
                            "company_name": company_name or "",
                            "job_title": job_title or "",
                            "salary": f"${salary_min:,.0f}/Y".replace(",", ".")
                                      if salary_min is not None else "",
                            "location": locate or "",
                            "deadline": deadline.strftime("%d/%m/%Y")
                                        if hasattr(deadline, "strftime")
                                        else str(deadline or ""),
                            "link_url": url or "",
                            "match_pct": int(similarity * 100)
                                         if similarity is not None else 0,
                            "description": description or "",
                            "rating": rating_statuses.get(job_id),
                        })
                except Exception as exc:
                    LOGGER.error("Job-card database fetch failed (%s)", type(exc).__name__)
                return loaded_jobs

            future = self._executor.submit(load_jobs)

            def publish_when_ready():
                if not future.done():
                    self.after(50, publish_when_ready)
                    return
                try:
                    loaded_jobs = future.result()
                except Exception as exc:
                    LOGGER.error("Background job-card load failed (%s)", type(exc).__name__)
                    loaded_jobs = []
                if scroll_frame.winfo_exists():
                    sample_jobs[:] = loaded_jobs
                    render_jobs()
                if on_complete is not None:
                    on_complete()

            self.after(50, publish_when_ready)
 
        # ── Search: call BrowsingForJobs → refresh from DB ────────────────
        def search_for_new_jobs():
            search_btn.configure(state="disabled", text="⏳  Searching…")
            for widget in scroll_frame.winfo_children():
                widget.destroy()
            ctk.CTkLabel(
                scroll_frame,
                text="⏳  Searching for new job openings, please wait…",
                font=("Segoe UI", 14),
                text_color=("gray45", "gray65"),
            ).grid(row=0, column=0, pady=60)
            self.update_idletasks()
 
            scrape_future = self._executor.submit(_call_browsing_for_jobs)

            def poll_search():
                if not scrape_future.done():
                    self.after(100, poll_search)
                    return
                if scrape_future.exception() is not None:
                    messagebox.showerror(
                        "Job search error",
                        "The job search could not be completed. See logs/app.log.",
                    )

                def enable_search():
                    if search_btn.winfo_exists():
                        search_btn.configure(
                            state="normal",
                            text="🔍  Search for new jobs",
                        )

                fetch_for_jobs(on_complete=enable_search)

            self.after(100, poll_search)
 
        # ── Search button (row 2) ─────────────────────────────────────────
        search_btn = ctk.CTkButton(
            self.main_cointeiner,
            text="🔍  Search for new jobs",
            height=36, corner_radius=18,
            font=("Segoe UI", 13, "bold"),
            command=search_for_new_jobs,
        )
        search_btn.grid(row=2, column=0, sticky="w", padx=24, pady=(2, 10))

        def clean_jobs():
            sample_jobs.clear()
            render_jobs()

        ctk.CTkButton(
            self.main_cointeiner,
            text="🗑  Clean jobs",
            height=36, corner_radius=18,
            font=("Segoe UI", 13, "bold"),
            fg_color="transparent",
            text_color=("gray50", "gray60"),
            hover_color=("gray88", "gray22"),
            command=clean_jobs,
        ).grid(row=2, column=0, sticky="e", padx=24, pady=(2, 10))
 
        # Initial load from DB on home-screen entry
        fetch_for_jobs()


# ─────────────────────────────────────────────────────────────────────────────
#  SHARED JOB CARD BUILDER
# ─────────────────────────────────────────────────────────────────────────────

    def _create_job_card(self, parent, row_idx: int, job: dict,
                         in_favorites: bool = False):
        company_logo_path = job.get("company_logo_path", "")
        company_name      = job.get("company_name", "")
        job_title         = job.get("job_title", "")
        salary            = job.get("salary", "")
        location          = job.get("location", "")
        deadline          = job.get("deadline", "")
        link_url          = job.get("link_url", "")
        match_pct         = job.get("match_pct", 0)
        description       = job.get("description", "")
        job_id = stable_job_identifier(
            job.get("id"),
            title=job_title,
            description=description,
            location=location,
        )
 
        card = ctk.CTkFrame(parent, corner_radius=16,
                            border_width=1, border_color=("gray80", "gray28"))
        card.grid(row=row_idx, column=0, sticky="ew", padx=8, pady=8)
        card.grid_columnconfigure(1, weight=1)
 
        # ── Logo ─────────────────────────────────────────────────────────
        logo_lbl = ctk.CTkLabel(
            card, text=company_name[:2].upper(),
            width=36, height=36, corner_radius=8,
            fg_color=("gray85", "gray25"), font=("Segoe UI", 12, "bold"),
        )
        logo_lbl.grid(row=0, column=0, padx=(16, 10), pady=(16, 4), sticky="nw")

        if company_logo_path:
            logo_future = self._image_executor.submit(
                _download_company_logo,
                company_logo_path,
            )

            def publish_logo_when_ready():
                if not logo_lbl.winfo_exists():
                    return
                if not logo_future.done():
                    self.after(100, publish_logo_when_ready)
                    return
                try:
                    image = logo_future.result()
                except Exception:
                    return
                logo_image = ctk.CTkImage(
                    light_image=image,
                    dark_image=image,
                    size=(36, 36),
                )
                logo_lbl.configure(image=logo_image, text="")
                logo_lbl.image = logo_image

            self.after(100, publish_logo_when_ready)
 
        # ── Header ───────────────────────────────────────────────────────
        hf = ctk.CTkFrame(card, fg_color="transparent")
        hf.grid(row=0, column=1, sticky="ew", pady=(16, 4))
        hf.grid_columnconfigure(0, weight=1)
 
        ctk.CTkLabel(hf, text=company_name, font=("Segoe UI", 11),
                     text_color=("gray45", "gray65"), anchor="w",
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(hf, text=job_title, font=("Segoe UI", 16, "bold"),
                     anchor="w").grid(row=1, column=0, sticky="w")
 
        # ── Match arc ────────────────────────────────────────────────────
        arc_size   = 54
        arc_canvas = tk.Canvas(card, width=arc_size, height=arc_size,
                               highlightthickness=0)
        arc_canvas.grid(row=0, column=2, padx=(8, 12), pady=(14, 4), sticky="ne")
 
        def _draw_arc(event=None, _c=arc_canvas, _p=match_pct):
            _c.delete("all")
            dark  = ctk.get_appearance_mode() == "Dark"
            bg    = "#2b2b2b" if dark else "#f9f9f9"
            track = "#444"    if dark else "#ddd"
            fill  = "#3fa87c" if _p >= 75 else "#e0a040" if _p >= 50 else "#e05050"
            _c.configure(bg=bg)
            cx = cy = arc_size // 2;  r = arc_size // 2 - 5
            _c.create_oval(cx-r, cy-r, cx+r, cy+r, outline=track, width=3, fill="")
            _c.create_arc(cx-r, cy-r, cx+r, cy+r, start=90,
                          extent=-360*_p/100, outline=fill, width=3, style="arc")
            _c.create_text(cx, cy, text=f"{_p}%",
                           font=("Segoe UI", 8, "bold"), fill=fill)
 
        _draw_arc()
        arc_canvas.bind("<Configure>", _draw_arc)
 
        # ── Separator ────────────────────────────────────────────────────
        ctk.CTkFrame(card, height=1, fg_color=("gray82", "gray26")
                     ).grid(row=1, column=0, columnspan=3, sticky="ew",
                             padx=16, pady=(4, 8))
 
        # ── Footer ───────────────────────────────────────────────────────
        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.grid(row=2, column=0, columnspan=3, sticky="ew",
                    padx=16, pady=(0, 14))
        footer.grid_columnconfigure((0, 1, 2), weight=1)
 
        ctk.CTkLabel(footer, text=f"💰 Salary:\n{salary}",
                     font=("Segoe UI", 11), justify="left", anchor="w",
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(footer, text=f"📍 Location:\n{location}",
                     font=("Segoe UI", 11), justify="left", anchor="w",
                     ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(footer, text=f"⏰ Deadline:\n{deadline}",
                     font=("Segoe UI", 11), justify="left", anchor="w",
                     ).grid(row=0, column=2, sticky="w")
 
        # ── Description panel (row 4 — hidden until ^ is clicked) ────────
        desc_frame = ctk.CTkFrame(card, fg_color=("gray92", "gray18"), corner_radius=8)
 
        if description:
            desc_box = ctk.CTkTextbox(
                desc_frame, height=110, wrap="word",
                font=("Segoe UI", 11), fg_color="transparent",
                activate_scrollbars=True,
            )
            desc_box.insert("0.0", description)
            desc_box.configure(state="disabled")
            desc_box.pack(fill="both", expand=True, padx=8, pady=8)
        else:
            ctk.CTkLabel(
                desc_frame,
                text="No description available.",
                font=("Segoe UI", 11),
                text_color=("gray50", "gray60"),
                anchor="w",
            ).pack(fill="both", expand=True, padx=14, pady=10)
 
        desc_state = {"visible": False}
 
        # ── Link  |  ^desc  |  ★fav ──────────────────────────────────────
        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.grid(row=3, column=0, columnspan=3, sticky="ew",
                        padx=16, pady=(4, 14))
        action_row.grid_columnconfigure(0, weight=1)
 
        ctk.CTkButton(
            action_row,
            text=f"🔗  {link_url if link_url else 'No link provided'}",
            anchor="w", font=("Segoe UI", 11),
            fg_color="transparent",
            text_color=("cornflowerblue", "deepskyblue"),
            hover_color=("gray90", "gray20"), height=26,
            command=lambda u=link_url: webbrowser.open(u) if u else None,
        ).grid(row=0, column=0, sticky="w")

        feedback_frame = ctk.CTkFrame(action_row, fg_color="transparent")
        feedback_frame.grid(row=0, column=1, sticky="e", padx=(4, 0))
        rating_state = {
            "value": job.get("rating") if job.get("rating") in ("great", "bad") else None,
            "pending": False,
        }
        rated_job = RatedJob(job_id, job_title, description, location)

        great_btn = ctk.CTkButton(
            feedback_frame,
            text="👍",
            width=36,
            height=36,
            corner_radius=18,
            font=("Segoe UI Emoji", 16),
            fg_color="transparent",
            hover_color=("gray88", "gray22"),
        )
        great_btn.grid(row=0, column=0, padx=(0, 2))

        bad_btn = ctk.CTkButton(
            feedback_frame,
            text="👎",
            width=36,
            height=36,
            corner_radius=18,
            font=("Segoe UI Emoji", 16),
            fg_color="transparent",
            hover_color=("gray88", "gray22"),
        )
        bad_btn.grid(row=0, column=1, padx=(2, 0))

        def _render_rating():
            great_active = rating_state["value"] == "great"
            bad_active = rating_state["value"] == "bad"
            great_btn.configure(
                fg_color=("#d8f3dc", "#14532d") if great_active else "transparent",
                text_color=("#166534", "#bbf7d0") if great_active else ("gray35", "gray75"),
                state="disabled" if rating_state["pending"] else "normal",
            )
            bad_btn.configure(
                fg_color=("#fee2e2", "#7f1d1d") if bad_active else "transparent",
                text_color=("#991b1b", "#fecaca") if bad_active else ("gray35", "gray75"),
                state="disabled" if rating_state["pending"] else "normal",
            )

        def _toggle_rating(requested: str):
            if rating_state["pending"]:
                return
            previous = rating_state["value"]
            next_rating = None if previous == requested else requested
            rating_state["value"] = next_rating
            rating_state["pending"] = True
            _render_rating()

            def finished(success: bool):
                try:
                    if not success:
                        rating_state["value"] = previous
                        messagebox.showerror("Rating error", "The rating could not be saved locally.")
                    rating_state["pending"] = False
                    _render_rating()
                except tk.TclError:
                    pass

            self._persist_rating_async(rated_job, next_rating, finished)

        great_btn.configure(command=lambda: _toggle_rating("great"))
        bad_btn.configure(command=lambda: _toggle_rating("bad"))
        _render_rating()
 
        desc_btn = ctk.CTkButton(
            action_row, text="⌄",
            width=36, height=36, corner_radius=18,
            font=("Segoe UI", 15, "bold"), fg_color="transparent",
            text_color=("gray50", "gray60"),
            hover_color=("gray88", "gray22"),
        )
        desc_btn.grid(row=0, column=2, sticky="e", padx=(4, 0))
 
        def _toggle_desc(_ds=desc_state, _df=desc_frame, _btn=desc_btn):
            _ds["visible"] = not _ds["visible"]
            if _ds["visible"]:
                _df.grid(row=4, column=0, columnspan=3,
                         sticky="ew", padx=16, pady=(0, 12))
                _btn.configure(text="⌃")
            else:
                _df.grid_remove()
                _btn.configure(text="⌄")
 
        desc_btn.configure(command=_toggle_desc)

        def _keyboard_activate(_event, command):
            command()
            return "break"

        for button, command in (
            (great_btn, lambda: _toggle_rating("great")),
            (bad_btn, lambda: _toggle_rating("bad")),
            (desc_btn, _toggle_desc),
        ):
            _enable_button_keyboard_focus(button)
            button.bind("<Return>", lambda event, cmd=command: _keyboard_activate(event, cmd), add=True)
            button.bind("<space>", lambda event, cmd=command: _keyboard_activate(event, cmd), add=True)
 
        try:
            initially_fav = pg_is_favorite(company_name, job_title)
        except Exception:
            initially_fav = in_favorites
 
        fav_state = {"active": initially_fav}
 
        fav_btn = ctk.CTkButton(
            action_row,
            text="★" if fav_state["active"] else "☆",
            width=36, height=36, corner_radius=18,
            font=("Segoe UI", 20), fg_color="transparent",
            text_color=("gold", "gold") if fav_state["active"] else ("gray50", "gray60"),
            hover_color=("gray88", "gray22"),
        )
        fav_btn.grid(row=0, column=3, sticky="e")
 
        def toggle_fav(_btn=fav_btn, _st=fav_state, _job=job, _card=card):
            _st["active"] = not _st["active"]
            _btn.configure(
                text="★" if _st["active"] else "☆",
                text_color=("gold", "gold") if _st["active"] else ("gray50", "gray60"),
            )
            try:
                if _st["active"]:
                    pg_add_favorite(_job)
                else:
                    pg_remove_favorite(_job.get("company_name", ""),
                                       _job.get("job_title", ""))
                    if in_favorites:
                        _card.destroy()
            except Exception as exc:
                messagebox.showerror("Database error", f"Could not update favorites:\n{exc}")
                _st["active"] = not _st["active"]
                _btn.configure(
                    text="★" if _st["active"] else "☆",
                    text_color=("gold", "gold") if _st["active"] else ("gray50", "gray60"),
                )
 
        fav_btn.configure(command=toggle_fav)


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    configure_logging(Path(resource_path("logs", "app.log")))
    root: JobFinderApp | None = None
    try:
        root = JobFinderApp()
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.mainloop()
        return 0
    except Exception as error:
        LOGGER.error("Application startup failed safely (%s)", type(error).__name__)
        print(
            f"JobFinder could not open the desktop window ({type(error).__name__}). "
            "See logs/app.log.",
            file=sys.stderr,
        )
        try:
            messagebox.showerror(
                "JobFinder startup error",
                "The desktop window could not be opened. See logs/app.log.",
                parent=root,
            )
        except Exception:
            pass
        if root is not None:
            try:
                root._on_close()
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
