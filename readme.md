# JobFinder

A desktop application that reads your resume (CV), extracts what matters from it with NLP, scrapes LinkedIn for matching job openings, scores each opening against your resume, and presents the best matches in a native GUI — with favorites, match percentage, and one-click access to the posting.

Everything runs locally as a Python desktop app backed by a PostgreSQL database.

# Disclaimer: This repo, actually works based on an simple CV already defined, so, initially, it'll only work for an unique curriculum. 

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| GUI | [CustomTkinter](https://customtkinter.tomschimansky.com/) (modern themed Tkinter) + Pillow |
| Web scraping | [Playwright](https://playwright.dev/python/) (sync API, headless Chrome) |
| NLP / ML | spaCy (`pt_core_news_md`), NLTK (stopwords, tokenization), python-docx |
| Translation | deep-translator (Google Translate, pt ↔ en) |
| Database | PostgreSQL via psycopg2 |
| Config | python-dotenv (`.env` file) |
| Packaging | PyInstaller (single executable, launched via `JobFinder.bat`) |

## Architecture

The system is organized into three Python packages, each with a single responsibility, plus PostgreSQL as the shared persistence and integration layer:

```
┌──────────────┐     imports      ┌────────────────┐     imports      ┌──────────┐
│   UI/        │ ───────────────► │ webscrapping/  │ ───────────────► │  ML/     │
│ CustomTkinter│                  │ Playwright     │                  │ spaCy +  │
│ desktop app  │                  │ LinkedIn       │                  │ NLTK     │
└──────┬───────┘                  └───────┬────────┘                  └────┬─────┘
       │                                  │                                │
       └───────────────┬──────────────────┴────────────────────────────────┘
                       ▼
              ┌─────────────────┐
              │   PostgreSQL    │
              │ jobs, curriculum,│
              │ favorites_jobs, │
              │ search_errors   │
              └─────────────────┘
```

### `UI/` — Desktop application (entry point)

`UI/main.py` is the application entry point. It builds a `JobFinderApp` (subclass of `ctk.CTk`) with an animated collapsible sidebar and three views:

- **Home** — job cards rendered from the `jobs` table, each showing company logo, title, location, description (expandable), a match-percentage arc drawn on a canvas, and a favorite toggle. A "search" action runs the whole scraping pipeline (`BrowsingForJobs`) in a background `threading.Thread` so the GUI stays responsive, then refreshes the cards from the database.
- **Favorites** — CRUD over the `favorites_jobs` table.
- **Curriculum** — upload/download/replace/delete of resume files. The `.docx` file is stored as a binary blob (`BYTEA`) in the `curriculum` table, which is how the ML layer picks it up.

The UI module also owns the schema: `_pg_ensure_tables()` creates `jobs`, `favorites_jobs`, and `curriculum` if they don't exist (the `curriculum` table includes an `embedding VECTOR(1536)` column, reserved for a future pgvector-based similarity upgrade).

### `webscrapping/` — LinkedIn scraper

`webscrapping/main.py` orchestrates the search pipeline:

1. **Query generation** — asks `ML.KeyWords()` for the most frequent meaningful words in the resume, picks two at random, translates them pt → en, and builds a LinkedIn Jobs search URL (`/jobs/search/`) with hardcoded preferences (locations, remote/on-site filters `f_wt`, seniority filters `f_E`). Failed searches are recorded in a `search_errors` table so they aren't retried.
2. **Scraping** — launches headless Chrome through Playwright, collects job IDs from `.job-search-card` elements, then visits each posting through LinkedIn's guest job-posting API (`/jobs-guest/jobs/api/jobPosting/{id}`) to extract title, company, location, logo, and full description.
3. **Filtering & scoring** — titles are normalized and checked against a blacklist (e.g. mechanical/electrical/thermal roles) and deduplicated; descriptions are translated en → pt in 5k-character chunks, then scored against the resume with `ML.ReturnSimilatity()`. Only jobs with **similarity ≥ 0.6** are kept.
4. **Persistence** — the `jobs` table is truncated and repopulated with the surviving openings (capped at 60).

### `ML/` — NLP / resume analysis

`ML/main.py` loads the latest resume blob from the `curriculum` table and provides two services:

- `KeyWords()` — parses the `.docx`, cleans it (whitespace, page numbers, a stop-list of generic resume words), lemmatizes with spaCy's Portuguese model, removes NLTK Portuguese stopwords, and returns the 10 most frequent remaining words. These seed the LinkedIn search query.
- `ReturnSimilatity(job_text)` — applies the same normalization/tokenization/lemmatization to a (translated) job description and returns the spaCy vector `Doc.similarity()` score between resume and job description.

At import time the module downloads NLTK corpora (`stopwords`, `punkt_tab`) and loads `pt_core_news_md`.

## Data Model (PostgreSQL)

| Table | Purpose |
|---|---|
| `curriculum` | Resume file (`file_data BYTEA`) plus extracted metadata columns (keywords, skills, seniority, `embedding VECTOR(1536)` — most reserved for future use) |
| `jobs` | Scraped openings: company, title, description, location, URL, logo, `similarity NUMERIC(5,2)` |
| `favorites_jobs` | User-favorited jobs (denormalized copy of card data) |
| `search_errors` | Search sentences that returned no LinkedIn results |

## End-to-End Flow

1. User uploads their `.docx` resume in the Curriculum view → stored in PostgreSQL.
2. User triggers a search → background thread runs `BrowsingForJobs()`.
3. ML extracts resume keywords → scraper builds a LinkedIn query → Playwright scrapes the postings.
4. Each posting is translated to Portuguese and scored against the resume; matches ≥ 60% are saved to `jobs`.
5. The Home view refreshes and renders the ranked job cards; the user favorites or opens postings in the browser.

## Setup

Requirements: Python 3.13, PostgreSQL, and Google Chrome (Playwright launches with `channel="chrome"`).

```bash
pip install -r requirements.txt
playwright install chromium
```

Create a `.env` file at the repository root:

```env
DB_NAME=your_db
DB_USER=your_user
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432
```

Run the app:

```bash
python UI/main.py
```

Tables are created automatically on first run. `JobFinder.bat` launches `pythonw main.py` from its own directory — it expects a root-level `main.py` (not tracked in the repo) that boots the UI. All modules resolve bundled resources via `sys._MEIPASS`, so the app also works when packaged with PyInstaller.

## Notes & Known Limitations

- `requirements.txt` is a full environment freeze and includes packages not used by this app (Django/DRF, transformers, torch, yt-dlp, etc.); the effective runtime dependencies are the ones listed in the Tech Stack table.
- LinkedIn scraping depends on the current guest-page DOM (XPath/CSS selectors) and may break when LinkedIn changes its markup.
- Similarity uses spaCy static word vectors; the `embedding VECTOR(1536)` column suggests a planned migration to transformer embeddings + pgvector.
- The resume pipeline currently assumes a Portuguese-language `.docx` resume.
