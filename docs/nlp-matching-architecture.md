# NLP Matching Architecture

## 1. Problem solved

The original NLP module mixed database access, document parsing, model initialization,
keyword extraction, and scoring at import time. Matching was based on one opaque spaCy
similarity number, so missing skills, experience, and seniority did not meaningfully
reduce a job's score.

## 2. Background context

The desktop UI calls `webscrapping.BrowsingForJobs`, which in turn relies on the public
`ML.KeyWords` and `ML.ReturnSimilatity` functions. LinkedIn card identifiers extracted
from `data-entity-urn` are an intentional part of the collection process and must remain
unchanged. The matching runtime must remain local and use free dependencies.

## 3. Decision taken

The ML implementation is divided into domain, extraction, scoring, application-service,
and infrastructure responsibilities. spaCy performs local tokenization while explicit
Portuguese/English aliases extract skills, tools, education, experience, and seniority.
The compatibility score is explainable and configurable, with defaults of 40% skills,
30% experience, 20% seniority, and 10% education/context. Legacy public functions are
retained as adapters so the UI and scraper can migrate without a breaking change.
Repository adapters load resumes from JSON, CSV, TXT, DOCX, or PostgreSQL and load job
records from JSON, JSONL, CSV, TXT, or a local folder. `python -m ML.cli` exposes the
complete local pipeline and writes JSON or CSV reports.

## 4. Consequences

Matching rules can be tested without PostgreSQL, LinkedIn, or translation services.
Results expose component scores, matched skills, missing skills, and reasons. The local
alias catalogue must be maintained as new professions and technologies are supported;
unrecognized requirements intentionally receive conservative scores rather than being
treated as confirmed matches.
Database connections and spaCy models are now initialized lazily. Normal CLI input and
format errors are logged instead of escaping as unhandled exceptions. The PostgreSQL
adapter selects the most recently uploaded curriculum deterministically.

### Scraping component

#### 1. Problem solved

The scraper previously combined random query generation, error-table writes, browser
navigation, external translation, matching, and per-row database commits. A failed run
could erase the currently displayed jobs, and translation made matching dependent on a
runtime API.

#### 2. Background context

LinkedIn search itself necessarily requires network access, but profile extraction and
compatibility scoring must be fully local. The established `.job-search-card` and
`data-entity-urn` identifier technique is reliable enough to retain.

#### 3. Decision taken

Search construction, Playwright collection, application orchestration, and PostgreSQL
persistence are separate modules. Queries are deterministic and derived from up to five
resume terms. Collection still converts each card URN into a guest-posting endpoint.
Descriptions are scored directly with bilingual local extraction rules; Google Translate
is no longer called. Selected jobs replace the database contents in one transaction only
after collection and scoring finish.

#### 4. Consequences

Collector and repository dependencies can be replaced with test doubles, so routine
tests need neither LinkedIn nor PostgreSQL. One malformed posting is logged and skipped.
LinkedIn selector changes remain an infrastructure risk, and job-search collection still
requires internet access even though the NLP pipeline and CLI do not.

### Verification

#### 1. Problem solved

The legacy matching and scraping code had no automated regression suite.

#### 2. Background context

Routine verification must not require PostgreSQL, LinkedIn, translation services, or a
downloaded language model. The requested coverage target is 80%, but this environment
does not currently contain `coverage.py` or the `pytest-cov` plugin.

#### 3. Decision taken

Offline tests use spaCy blank pipelines and in-memory test doubles for browser cards,
repositories, collectors, and database connections. Tests cover bilingual extraction,
weighted scoring, false-positive rejection, input adapters, JSON reporting, URL building,
the card-identifier mechanism, orchestration, and persistence. The CLI sample pipeline
was also executed three consecutive times.

#### 4. Consequences

The current suite contains 14 passing tests and all three repeated sample runs completed
without errors. A numeric coverage claim and false-positive-rate claim remain pending
until coverage tooling and a labeled validation set are supplied. Live selector and
PostgreSQL smoke tests remain acceptance-environment checks rather than routine tests.

### LinkedIn empty-result recovery

#### 1. Problem solved

LinkedIn sometimes did not render `.job-search-card` within ten seconds. Playwright then
raised `TimeoutError`, so a slow, empty, consent-gated, or structurally changed result
page stopped the complete search.

#### 2. Background context

The card code stored in `data-entity-urn` remains the required way to identify postings.
LinkedIn can expose that attribute through both job-specific and generic base-card
wrappers, and a query containing too many resume terms can legitimately return no jobs.

#### 3. Decision taken

Card discovery now waits for the original selector or two compatible wrappers, handles
common consent b—> mean: devides an LLM into several slices, which are, actually, experts. Besides that use also a routing system to activate these experts models. It is an usuful approach to decrease computational effort, because only uses requirement resources for certain tasks.uttons, and records the final page title, URL, and heading when no card
appears. An empty page returns an empty collection instead of raising. The application
then retries deterministic searches using three, two, and one resume terms.

#### 4. Consequences

Slow or empty LinkedIn pages no longer produce an unhandled Playwright timeout. Card IDs
are still extracted exclusively from `data-entity-urn`. If all three searches are empty,
the operation logs an error and preserves the existing jobs table rather than truncating
it. Tests cover query broadening and database-preservation behavior.

### Final posting URL resolution

#### 1. Problem solved

Collected jobs retained the LinkedIn guest API endpoint instead of the public job page
that users should open from the desktop application.

#### 2. Background context

The guest response exposes a clickable H2 title at
`/html/body/section/div/div[1]/div/a/h2`. LinkedIn resolves the public posting URL only
after that title is activated.

#### 3. Decision taken

The collector first captures the title, description, company, location, and logo. URL
resolution is deliberately last: it clicks the supplied H2 XPath with a three-second
click timeout, waits 1.5 seconds for the page response, and then reads `page.url`.

#### 4. Consequences

Persisted jobs normally contain the public LinkedIn posting URL. If the title is missing
or the click fails, the failure is logged at INFO level and the guest endpoint remains a
usable fallback. The offline suite contains 16 passing tests, including URL resolution
and fallback behavior.
