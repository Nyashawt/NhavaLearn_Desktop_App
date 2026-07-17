# AI Integration — Handoff Notes

**Status:** first working slice, not yet fully wired into the teacher-facing UI
**Audience:** co-developer picking this up to review/continue
**Date:** 2026-07-17
**Relates to:** `TECHNICAL.md` (no AI section there yet — this doc is the interim
reference until it's folded in)

---

## 1. What was added

AI generation backend for quizzes, flashcards, and lesson-plan drafts, following
the same architecture rules as the rest of the app (§5/§10 of `TECHNICAL.md`):
every method still returns `{"ok": ...}`, role/ownership checks still happen in
`api.py`, nothing new touches a public `Api` attribute.

New files:

| File | Purpose |
|---|---|
| `app/ai_generator.py` | Cloud (Anthropic) + local (llama.cpp/GGUF) generation, cloud-first/local-fallback switch, three content-type functions (`generate_quiz`, `generate_flashcards`, `generate_lesson_draft`) |
| `app/ai_settings.py` | Thin read/write wrapper over new `settings` columns |
| `app/ai_html_formatter.py` | Converts the model's free-text output into Quill-compatible HTML (`div.ql-editor` wrapper — see §7's rendering-parity rule) |
| `app/ai_pagination.py` | Handles truncated generations by auto-continuing until the requested item count is reached |
| `tests/test_ai_generator.py` | Headless tests for the formatter + the cloud/local switch (mocks generation, no API key or GGUF file needed) |

Changed files:

- `app/db.py` — added `ai_provider`, `ai_api_key`, `ai_model_filename` columns to
  `settings` via the existing `MIGRATIONS` list (zero-step, same pattern as §4).
- `app/api.py` — new bridge methods: `ai_get_status`, `ai_generate_quiz`,
  `ai_generate_flashcards`, `ai_generate_lesson_draft`, `get_ai_settings`,
  `update_ai_settings`.
- `requirements.txt` — added `llama-cpp-python==0.2.55` and `anthropic>=0.40.0`.
- `ui/js/app.js` — new **AI Settings** admin screen (`routes.aiSettings`, nav
  link visible to admins only); one **"Generate with AI"** button in the lesson
  editor wired to `ai_generate_quiz` only (see gaps below).

## 2. How generation works (runtime behavior)

- `ai_generator.smart_generate()` is cloud-first: if `provider` is `cloud` or
  `auto` and an API key is set, it calls Anthropic (`claude-sonnet-4-6`). On
  failure it falls back to local **only** if `provider == "auto"`; if
  `provider == "cloud"` and the call fails, it raises rather than falling back.
- Local generation lazy-loads a GGUF model on first use (not at app startup) via
  `llama-cpp-python`, CPU-only (`n_gpu_layers=0` hard-coded — matches the fixed
  no-GPU hardware target in §1). Model file comes from
  `%LOCALAPPDATA%\NhavaLearn\models\`; if no filename is configured it
  auto-picks the largest `.gguf` present.
- If neither is available, `AIUnavailable` is raised with a teacher-facing
  message pointing at Admin → AI Settings.
- Quiz/flashcard generation goes through `ai_pagination.py`, which detects
  truncated output and re-prompts for the remainder (up to 3 attempts) — needed
  because local models on this hardware have a real context/output ceiling.

## 3. What to check on your side

You'll need a Windows machine with WebView2 (same as any other GUI change —
§11 applies: `python tests/test_backend.py` doesn't exercise real rendering).

1. **Pull, then `python -m pip install -r requirements.txt`** — two new
   dependencies were added; `llama-cpp-python` has a compiled wheel, flag it to
   me immediately if it fails to install on your machine (CPU/AVX support
   varies by machine, and this is the number one risk in this change).
2. **Run both headless suites** before touching anything else:
   ```
   python -m tests.test_backend
   python -m tests.test_ai_generator
   ```
   Both should be fully green. If `test_ai_generator` fails on a `patch()`
   target, that's the same class of bug I already fixed once — a mock target
   not matching the real import path.
3. **Admin → AI Settings screen** (`python main.py --debug`, log in as admin):
   - Set provider to "Cloud (Anthropic) only", leave no key → try generating a
     quiz as a teacher → should get a clean "AI is not available…" error, not a
     crash.
   - Enter a real Anthropic API key, save, reload the screen → key should show
     as "Key Configured" and the input should stay blank (never round-trips the
     real key back to the UI — check the network/JS layer doesn't leak it
     either).
   - Set a `model_filename` that doesn't exist in `models\` → confirm the error
     message names the missing file rather than a raw traceback.
4. **Actual generation** — with a real API key configured, generate a quiz from
   the lesson editor ("Generate with AI" button). Check: the inserted HTML
   renders correctly in the editor, survives switching to Preview, and survives
   a real presentation (this exercises the `div.ql-editor` rendering-parity
   rule from §7 — flag anything that looks misaligned).
5. **Local model path** — this is unverified against a real GGUF file (I only
   confirmed the "no model found" error path). If you have a small GGUF handy,
   drop it in `%LOCALAPPDATA%\NhavaLearn\models\`, set provider to "local" or
   "auto", and confirm generation actually completes on this hardware in
   reasonable time. This is the biggest untested surface in the whole change.

## 4. Known gaps (not yet done, flagging so you don't assume otherwise)

- **Flashcards and lesson-draft generation have no UI entry point.** Backend
  functions exist and are unit-tested, but only the quiz path is wired to a
  button in the editor.
- **No model download/install flow.** Unlike PhET/GeoGebra (§8), there's no
  admin UI to fetch a GGUF file — you have to copy one into the models folder
  by hand. If we want local generation to be realistically usable on a
  no-internet school laptop, this needs the same "admin installs an asset"
  pattern as GeoGebra's runtime install.
- `update_ai_settings` treats an empty string as "don't change this field" (it
  passes `None` through), so there's currently no way to *clear* a previously
  set model filename back to "auto-pick" from the UI — you'd have to set it to
  a blank string and have that actually persist as NULL, which it doesn't yet.
  Minor, but worth knowing before you rely on it.

## 5. Test data note

All screens were driven and verified live against the real app (WebView2
rendering, real SQLite writes, real bridge calls) with a throwaway admin
account and a fake API key — that test data was cleaned up afterward, so
`%LOCALAPPDATA%\NhavaLearn` is back to a fresh-install state on this machine.
