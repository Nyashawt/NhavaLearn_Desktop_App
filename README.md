# NhavaLearn Desktop (working name — final name TBD, see blueprint)

Offline-first lesson creation and presentation app for the Nhava SmartClass Kit.
Single-process PyWebView app, SQLite storage, no internet required — ever.

## Run in development (Windows 11)

```
pip install -r requirements.txt
python main.py          # add --debug to open devtools
```

Requires the WebView2 runtime, which ships with Windows 11 — nothing else to install.

## First launch

A one-time setup wizard asks for the school name, location, and the
administrator account. After that, the app opens on the sign-in screen.

## Where data lives

`%LOCALAPPDATA%\NhavaLearn\nhavalearn.db` (SQLite, WAL mode for resilience
against power loss) plus a `media\` folder reserved for file-based media.

## Roles

- **Admin** — one-time setup, manages accounts, school settings, subjects
- **Teacher** — creates/manages/presents *their own* classes and lessons
- **Supervisor** — read-only view of all classes and lessons across teachers

Role checks are enforced in the Python bridge (`app/api.py`), not just the UI.

## Presenting

"Present" opens a clean fullscreen window on the second display (the projector
over HDMI). The teacher's screen keeps a floating control bar — previous/next
page (also arrow keys) and end presentation.

## Bundled vendor libraries (ui/vendor — no CDN)

- Quill 2.0.2 + @enzedonline/quill-blot-formatter2 (drag-resize images)
- KaTeX 0.17 (+ auto-render): type `$$ ... $$` in a page for equations
- Bootstrap Icons 1.13

## Videos in lessons

The film icon in the editor toolbar opens a native file picker (.mp4/.webm/.m4v).
The file is COPIED into `%LOCALAPPDATA%\NhavaLearn\media\` — so lessons keep
working after the source USB stick is unplugged — and embedded as a playable
video in the page, the preview, and the presentation.

## Presentation controls

Three ways to navigate while presenting: the floating bar on the teacher's
screen, arrow keys / PageUp / PageDown / Space in the presentation window, and
on-screen touch arrows (for driving the lesson from the interactive board).
Esc ends the presentation. Cursor and arrows fade after 3s idle.

## Simulations (PhET)

Admin → Simulations manages a shared library available to every teacher:
- **Install from file** — any PhET single-file .html sim or GeoGebra .ggb file
- **PhET catalog** — with the internet dongle connected, browse/search the
  full PhET catalog and download sims directly; they run offline forever after.
  Catalog access uses PhET's Partner Metadata API under the organisation's
  licence agreement with PhET. Sims are CC-BY-NC (attribution shown in-app).

## GeoGebra

.ggb files play inside lessons once the runtime is installed: download the free
"GeoGebra Math Apps Bundle" zip from geogebra.org/download on any connected
machine, then Admin -> Simulations -> Install GeoGebra runtime and pick the zip
(installed under the app's media folder, works offline forever after). Until
then, .ggb files are stored but pages show a "runtime not installed" notice.
GeoGebra use in the commercial kit operates under the organisation's GeoGebra
licence.

Teachers add sims to lessons via "Add simulation" in the editor's page panel.
Simulation pages render full-bleed and fully interactive when presented —
including touch on the interactive board.

## Tests (assessments)

Each class has Lessons and Tests. Teachers build tests from four question
types (multiple choice, true/false, short answer, long answer) with marks and
optional explanations. **Present** shows questions only (students answer on
paper); **Review with answers** re-presents with correct answers and
explanations revealed for whole-class marking. Supervisors can view any test
including the answer key.

## Test suite

`python tests/test_backend.py` exercises the whole permission/data layer
headlessly (no window needed) — run it after changing `app/api.py`.

## Known v1-skeleton limitations (deliberate, next iterations)

- Images are stored base64-inline in page HTML (Quill's default). Fine to
  start; move to file-based media (the `media` table exists) if lessons get
  image-heavy, to keep the DB small.
- Deleting a lesson does not yet clean up its copied video files from media\.
- No packaging yet — PyInstaller one-file build is the plan for deployment.
