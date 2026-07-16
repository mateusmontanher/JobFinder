# Packaged Desktop Search Runtime

## 1. Problem solved

The previous executable omitted the gettext catalogs, static browser files, and a
Playwright Chromium binary. Its language menu therefore had only English available,
and a failed browser launch was converted to an empty result before the desktop UI
reloaded the previously saved jobs. This made a failed search appear to return the
same jobs again.

## 2. Background context

JobFinder's native interface discovers translations from `locales/` at runtime and
uses Playwright only to collect LinkedIn search-card identifiers. PyInstaller does not
automatically package source-tree data files or Playwright's separately installed
browser cache. A standalone executable must not rely on Chrome or an existing
Playwright installation on the recipient's computer.

## 3. Decision taken

`main.spec` explicitly packages the complete locale tree, browser-view static assets,
the native user image, Playwright's required data, and the locally installed Chromium
cache. When frozen, `LinkedInCollector` launches that bundled Chromium executable;
source runs still use the configured system Chrome channel. The search facade now
raises a contained `JobSearchFailed` after logging an infrastructure failure, allowing
the existing desktop error dialog to distinguish a failed search from an empty result.

## 4. Consequences of this change

The rebuilt executable can switch between English and Brazilian Portuguese and can
start a browser engine without external browser setup. A LinkedIn, database, or browser
failure is visible to the user and no longer masquerades as fresh saved results. The
single-file executable is larger and takes longer to unpack at startup because it
contains Chromium; this is the trade-off for an independently functional distribution.
