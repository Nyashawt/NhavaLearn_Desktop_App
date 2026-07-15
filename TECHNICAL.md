# NhavaLearn Desktop — Technical Documentation

**Status:** v1.5 · working name pending final naming decision (see blueprint)
**Audience:** developers joining the project
**Last updated:** July 2026

---

## 1. What this is

An offline-first Windows desktop app for the **Nhava SmartClass Kit** — a solar-powered
classroom kit (laptop + touch-enabled projector) deployed to rural schools with no
grid electricity and no internet. Teachers create rich lessons (text, images, video,
equations, interactive simulations) and present them full-screen on the projector.

It is a **ground-up standalone product**, not a port of the company's Fundo LMS web
platform. Fundo assumes many devices reaching a server over the internet; this app
assumes exactly one laptop, forever offline. It deliberately inherits Fundo's *visual
identity and UX patterns* (documented in `Nhava_Technical_Reference`) but none of its
PHP/MySQL/WebSocket architecture. Resist importing multi-tenant or multi-school
patterns anywhere — one kit serves one school, permanently.

Hardware target: i5-12450H, 16 GB RAM, 1 TB SSD, Windows 11 Pro, no GPU.
Projector connects over HDMI as a second display (touch-enabled via stylus).
Occasional internet is possible via a 4G USB dongle (used only for admin tasks
like downloading PhET sims — never assumed during teaching).

## 2. Architecture in one paragraph

A **single Python process**. [pywebview](https://pywebview.flowrl.com/) opens a native
WebView2 window rendering a vanilla-JS single-page app (`ui/`). The frontend calls
Python through pywebview's JS bridge (`app/api.py` — the only API surface). Data
lives in a single **SQLite** file. A second in-process HTTP server (**bottle**, on an
ephemeral localhost port) serves lesson media and the GeoGebra runtime. Presenting
opens a second frameless fullscreen pywebview window on the projector's display.
No external services, no separate processes, one installer. This was a deliberate
decision over a two-process design: there is no on-site technical support, so
"fewer moving parts" beats architectural purity.

```
┌────────────────────────── python process ──────────────────────────┐
│  main.py                                                           │
│   ├─ webview main window ── ui/index.html + app.js (SPA)           │
│   │        │  js_api bridge (app/api.py: auth, CRUD, roles)        │
│   ├─ webview present window ── ui/present.html (projector)         │
│   ├─ app/media_server.py ── bottle @ 127.0.0.1:<ephemeral>         │
│   │        ├─ /media/<path>   videos, sims, GeoGebra runtime tree  │
│   │        └─ /ggb/<file>     generated GeoGebra viewer page       │
│   └─ SQLite @ %LOCALAPPDATA%\NhavaLearn\nhavalearn.db (WAL)        │
└────────────────────────────────────────────────────────────────────┘
```

## 3. Repository layout

```
nhavalearn/
├── main.py                entry point; creates the main window
├── requirements.txt       pywebview, bottle — that's all
├── app/
│   ├── api.py             THE bridge: every UI→Python call lives here
│   ├── auth.py            PBKDF2-HMAC-SHA256 password hashing (stdlib only)
│   ├── db.py              schema, connection factory, data dir resolution
│   └── media_server.py    bottle media server + GeoGebra viewer route
├── ui/
│   ├── index.html         SPA shell; loads bundled vendor libs
│   ├── present.html       projector window (self-contained, no app.js)
│   ├── css/app.css        Fundo brand system (see §9)
│   ├── js/app.js          entire SPA: router, views, editor, presenting
│   └── vendor/            Quill 2.0.2, blot-formatter2, KaTeX, Bootstrap
│                          Icons — bundled locally, NO CDN references ever
└── tests/test_backend.py  headless test suite (see §11)
```

## 4. Data model (SQLite)

`%LOCALAPPDATA%\NhavaLearn\nhavalearn.db`, WAL mode (chosen for resilience to
sudden power loss on solar). Schema is created with `CREATE TABLE IF NOT EXISTS`
on every launch, so adding tables is a zero-step migration for existing installs.

| Table | Purpose | Notes |
|---|---|---|
| `settings` | single row (id=1): school name, location, setup flag | replaces Fundo's whole schools table |
| `users` | accounts | `role IN ('admin','teacher','supervisor')`, soft-deactivate via `active` |
| `subjects` | subject list | seeded with Zimbabwe curriculum defaults; admin can add |
| `classes` | owned by one teacher (`teacher_id`) | ownership is the permission boundary |
| `lessons` | belongs to class + teacher + optional subject | |
| `lesson_pages` | ordered pages; `page_type IN ('content','simulation')` | `content_html` = Quill output; `sim_path` = filename in media dir for sim pages |
| `media` | record of copied video files | files live in `…\NhavaLearn\media\` |
| `sims` | admin-managed simulation library | `kind IN ('phet','ggb','html')`, `filename` unique |

## 5. The JS↔Python bridge — conventions and one critical rule

Every method in `class Api` returns a JSON-serialisable dict:
`{"ok": True, …data}` or `{"ok": False, "error": "human-readable message"}`.
Error messages are written for teachers, not developers.

**Role enforcement happens in Python, not the UI.** `self._require(*roles)` at the
top of every mutating method is the real permission check; UI hiding buttons is
cosmetic. Three roles, hard-coded checks — Fundo's granular `role_functions`
system was deliberately not ported. Ownership checks (`_owns_class`,
`_owns_lesson`) gate teachers to their own content; supervisor is read-only
across everything; admin manages accounts/settings/sims but does not author.

**⚠ THE UNDERSCORE RULE (do not break this):** pywebview builds the JS bridge by
crawling every **public** attribute of the api object. Storing a window object
in a public attribute sends the crawler into the native .NET object graph and
crashes startup with infinite recursion (we hit this in production: the
`.Bounds.Empty.Empty…` bug — slow startup, blank screens, intermittent failure
to launch). **All internal state on `Api` must be underscore-prefixed**
(`_main_window`, `_present_window`, `_current_user`, …). The test suite asserts
`Api` has zero public data attributes; keep that test green.

## 6. Media serving — why it exists and how URLs stay valid

pywebview serves the UI over a local HTTP server, and an `http://` page cannot
load `file://` resources — so all media goes through our own bottle server
(`app/media_server.py`) on an **ephemeral** localhost port. Bottle's
`static_file` supports HTTP Range requests, which `<video>` needs for seeking.

Because the port changes every run, **lesson HTML never stores absolute media
URLs**. Videos are embedded as `<video data-filename="…">`; every render path
(editor, preview, viewer, presentation) calls `fixVideoSrcs()` /
`simSrc()` to rebuild `src` from the current base URL fetched at boot
(`api.get_media_base()`). If you add a new media type, follow this pattern:
store the filename, rebuild the URL at render time.

Videos are **copied** into the media dir on insert (not referenced in place) so
lessons survive the source USB stick being unplugged.

## 7. Presenting — dual window and the sync protocol

`start_presentation(lesson_id)` opens `ui/present.html` fullscreen on
`webview.screens[1]` (falls back to primary screen with a toast if no second
display). The presentation window is fully self-driving: arrow keys / PageUp /
PageDown / Space navigate, Esc or the on-screen ✕ ends, semi-transparent touch
arrows serve the stylus-at-the-board case. Cursor and controls fade after 3 s
idle.

Two-way sync, all via Python (windows never talk to each other directly):
- teacher's bar → `api.present_goto(i)` → `present_window.evaluate_js("showPage(i)")`
- board navigation → `showPage()` calls `api.present_report(i)` →
  `main_window.evaluate_js("presenterSync(i)")` updates the bar
- any close path fires `_on_present_closed` → `presenterEnded()` in the main window

`present.html` is deliberately self-contained (own CSS/JS, no app.js) — it must
render *only* clean lesson content, no app chrome, per the Fundo pattern.

**Rendering parity rule:** saved lesson HTML is styled by Quill classes and
blot-formatter2 alignment classes. Any surface that displays lesson content must
load `quill.snow.css` + `quill-blot-formatter2.css` AND wrap content in
`<div class="ql-editor">` — **specifically a `div`**: blot-formatter2 scopes all
alignment rules as `div.ql-editor …` (we shipped a misalignment bug by using
`<main class="ql-editor">`).

## 8. Simulations

### PhET
Admin-managed library (`sims` table + Admin → Simulations page). Two install
paths: local `.html` file (offline), or the **PhET catalog** — fetches
`phet-api.colorado.edu/partner-services/2.0/metadata/simulations?locale=en`
(needs the dongle), searchable, one-click download of the single-file English
sim from `phet.colorado.edu<runUrl>`. The downloader **only** accepts paths
matching `/sims/html/…*.html` — never fetch arbitrary URLs. Downloaded sims are
self-contained and run offline forever. Teachers add them via "Add simulation"
in the editor; sim pages render as iframes (interactive in editor/preview/viewer,
full-bleed in presentation).

### GeoGebra
`.ggb` files need GeoGebra's web runtime. The runtime is **not** shipped with
the app: admin downloads the official **Math Apps Bundle** zip
(`https://download.geogebra.org/package/geogebra-math-apps-bundle` — note: this
is different from the desktop app installer .exe) and installs it via Admin →
Simulations. `_extract_ggb_runtime()` validates the zip (must contain
`deployggb.js`; rejects path-traversal members) and extracts to
`media/geogebra/`. `media_server.find_ggb_runtime()` locates `deployggb.js` by
walking the tree — never assume the zip's internal layout.

**Same-origin design:** GeoGebra's runtime fetches the `.ggb` file via XHR,
which is CORS-blocked cross-origin. So `.ggb` pages don't embed the file
directly — they iframe `/ggb/<filename>` on the **media server**, a generated
viewer page that loads the runtime, sets the codebase, and injects the applet,
all same-origin. Debugging a GeoGebra page starts at that URL.

### Licensing (must stay resolved before shipping)
- PhET Metadata API: requires a license agreement with PhET (in place). Sims
  are CC-BY-NC — attribution is shown on the Simulations page; keep it.
- GeoGebra: free for non-commercial use; commercial kit usage operates under
  the organisation's GeoGebra license (in place).

## 8b. Tests (assessment) subsystem

Teachers create tests per class (`tests` + `test_questions` tables). Four
question kinds: `multiple_choice` (options JSON + correct index in `answer`),
`true_false` (`answer` = "true"/"false"), `short_answer` / `long_answer`
(`answer` = model answer). Each question carries `marks` and an optional
`explanation`.

**Key design: tests reuse the lesson presentation machinery.**
`api.test_pages(test_id, reveal)` renders the test server-side into
presentation-shaped pages (a cover page from title/instructions/totals, then
one page per question, all values HTML-escaped — there's an XSS test).
`start_test_presentation` feeds those pages to the same `_open_presentation`
helper lessons use, so navigation/touch/sync/exit come for free.

Two modes: **Present** (`reveal=False` — questions only, answer spaces shown)
and **Review** (`reveal=True` — correct options ticked green, model answers and
"Why" explanation boxes shown) for going through the test with the class after
they've answered on paper. The in-app read-only viewer (`testViewer` route,
used by supervisors) renders the same generated pages with the key visible.
Builder UI (`testEditor` route) follows the lesson editor's page-panel pattern:
drag-reorder, auto-save on switch, per-kind form fields.

## 9. Frontend notes

Vanilla JS, no framework, no build step — a deliberate choice for a codebase
that must be maintainable for years with minimal tooling. `app.js` structure:
utils → Quill extensions (registered once) → hash-free router (`routes` object,
`go(name, …args)`) → one function per view → presenting globals.

Quill setup mirrors Fundo: Quill 2.0.2 + `@enzedonline/quill-blot-formatter2`
(drag-resize images — this plugin is *the* reason the editor feels right),
superscript/subscript buttons, custom `LocalVideo` block embed (value =
filename, see §6). Equations: KaTeX, authored as `$$…$$`, rendered by
auto-render in preview/viewer/presentation (not a Quill module — matches Fundo).

Visual identity (from the Fundo reference, applied via CSS variables in
`app.css`): primary blue `#2563EB`, purple `#7C3AED`, cyan `#06B6D4`, gold
accent `#FDE68A` (reserved for the single most important element per screen),
navy `#0F172A`; signature 135° blue→purple→cyan gradient for heroes/headers;
white cards with soft shadows; empty-states with icon + sentence; Bootstrap
Icons throughout.

## 10. Security posture (local-trust model)

Threat model: physical access to a shared school laptop — not remote attackers.
- Passwords: PBKDF2-HMAC-SHA256, 200k iterations, per-user salt (stdlib only).
  Hashes are non-reversible; DB access ≠ password recovery.
- All role/ownership checks server-side in `api.py`.
- Both HTTP servers bind 127.0.0.1 only.
- Zip extraction guards against path traversal; PhET downloads validate URL shape.
- Not defended (accepted): a local admin user with DB file access can edit data
  directly — that's inherent to the single-machine model.

## 11. Development & testing

Setup: Python 3.12+ on Windows 11, `python -m venv .venv`, activate,
`python -m pip install -r requirements.txt` (use `python -m pip`, the pip.exe
shim breaks on some machines), then `python main.py --debug` (right-click →
Inspect for devtools). Data lives outside the repo — delete
`%LOCALAPPDATA%\NhavaLearn` to reset to first-launch.

`python tests/test_backend.py` runs the whole permission/data layer headlessly
(webview is stubbed): setup wizard, auth, ownership boundaries, supervisor
read-only, pages, reordering, sims library, PhET download validation, GeoGebra
runtime install/traversal/viewer-route, and the bridge-safety assertion (§5).
Run it after any change to `app/`. GUI behaviour (WebView2 rendering, dialogs,
second-screen windows, sim execution) can only be verified on real Windows —
plan for that in every change.

## 12. Hard-won lessons (read before "improving" things)

1. **Never put non-underscore attributes on `Api`** — infinite recursion at
   startup (§5). There's a regression test.
2. **`file://` URIs don't work** — the UI is served over http; all media must
   go through the media server (§6).
3. **Ephemeral port ⇒ never persist absolute media URLs** — store filenames,
   rebuild at render (§6).
4. **Lesson-content surfaces need `div.ql-editor`** — a `<main>` with the same
   class silently breaks image alignment (§7).
5. **GeoGebra must be same-origin with its `.ggb` files** — hence the generated
   `/ggb/` viewer route (§8).

## 13. Current limitations / roadmap

- **No installer yet** — runs from source; PyInstaller one-file build is the
  next milestone and the gate to a real-teacher pilot.
- Images stored base64-inline in page HTML (Quill default); migrate to
  file-based media (pattern from §6) if lessons become image-heavy.
- Deleting a lesson doesn't clean up its copied video files.
- No backup/restore — a term of lessons lives in one file on one laptop;
  USB backup is designed and queued.
- GeoGebra runtime tested against a synthetic bundle in CI-style tests; real
  bundle verified manually on hardware.
- Product naming ("NhavaLearn" clashes with the existing Fundo web platform
  name) still needs a final decision before anything public-facing.

## 14. Deferred / rejected decisions (don't relitigate without new facts)

- Two-process architecture (separate service + app): rejected — installer and
  reliability simplicity wins with no on-site support.
- Full vector DB, multi-school flows, Fundo's `role_functions`, WebSocket sync,
  marketplace features: all rejected as inapplicable (see blueprint §5).
- v2 (offline RAG content generation) is specced in the blueprint but
  deliberately untouched until v1 has been used by a real teacher.
