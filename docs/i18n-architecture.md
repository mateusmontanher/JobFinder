# Internationalization Architecture

## 1. Problem solved

The native desktop UI, optional localhost browser view, and command-line help exposed
English strings directly from presentation code. Portuguese users could not select a
language, and adding another language would have required changing Python or browser
code.

## 2. Background context

JobFinder is a local Python application with a CustomTkinter entry point and a small,
loopback-only browser view served by the desktop process. Localization therefore has
to work without SaaS, a JavaScript build chain, or network access. It must also preserve
the browser API's strict route and request allowlists, PyInstaller resource lookup, and
the existing English wording.

The extraction configuration currently finds 103 source messages. The Portuguese
catalog translates all 103, plus the catalog's native language name metadata. Tests
compare the template and catalog so untranslated or obsolete application messages are
detected before release.

## 3. Decision taken

The `jobfinder.i18n` package separates translation policy from catalog storage:

- `TranslationService` detects and normalizes OS locales, selects English as the
  fallback, performs singular and plural lookup, notifies presentation listeners after
  runtime changes, and warns once for a missing key.
- `BabelPoCatalogRepository` discovers locale directories dynamically. It loads GNU
  `.mo` catalogs through the Python standard library for fast startup and can compile a
  newly added `.po` catalog in memory with Babel when no `.mo` exists.
- CustomTkinter owns only widget rebuilding and preserves the active view while the
  sidebar language selector changes locale.
- The browser receives an authenticated, fixed allowlist of messages from
  `/api/i18n/<locale>`. JavaScript updates text, accessible labels, pluralized counts,
  and the document `lang` attribute without navigation or reload. The endpoint accepts
  only locale-shaped paths and does not expose arbitrary catalog keys or filesystem
  access.
- Python and JavaScript source strings plus marked static HTML are extracted through
  `babel.cfg`. Catalogs use the standard gettext `messages` domain under
  `locales/<locale>/LC_MESSAGES/`.
- Local PyInstaller builds must include the complete `locales` tree. English remains the
  source catalog, so missing translations display the original text rather than failing
  startup. Packaging recipes and executables remain ignored local artifacts.

Language failures are logged at WARNING without user data. Dynamic job titles,
descriptions, locations, file names, database values, URLs, and exception details are
not sent to translation logs or treated as interface messages.

## 4. Consequences of this change

Portuguese OS locales start in pt-BR automatically, unsupported locales remain in
English, and both front ends can switch language at runtime. ARIA labels and the browser
document language change with visible text, preserving keyboard interaction and screen
reader context. CLI help follows the detected locale as well.

Adding a language is catalog-only: place a valid `jobfinder.po` in a new locale
directory. Compiling it is optional for source execution but recommended for packaged
startup performance. Catalog loading is cached and covered by a sub-50 ms performance
test; malformed catalogs fall back safely and emit a warning.

Unit tests cover locale detection, fallback, pluralization, dynamic catalog discovery,
catalog completeness, warning behavior, and load performance. Integration tests cover
the authenticated translation API, a live CustomTkinter language switch, and browser
language/ARIA/plural updates. The repository coverage gate is 90 percent on the scoped
application services, and CI runs on Python 3.12.
