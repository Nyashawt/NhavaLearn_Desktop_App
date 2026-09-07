"""The JS <-> Python bridge exposed to the UI via pywebview's js_api.

Every method returns a plain dict (JSON-serialisable). Convention:
  {"ok": True, ...data}  on success
  {"ok": False, "error": "human-readable message"}  on failure

Role enforcement happens HERE, server-side of the bridge — the UI hiding a
button is cosmetic, this layer is the real permission check. Three roles,
hard-coded checks (deliberately not porting Fundo's role_functions system).
"""
import os
import html
import json
import urllib.parse
import urllib.request
import zipfile
import shutil
import time

import webview

from . import auth, db, media_server
from .paths import resource_path


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


class Api:
    def __init__(self):
        self._current_user = None       # dict: id, username, full_name, role
        self._main_window = None        # set by main.py after window creation
        self._present_window = None
        self._present_pages = []
        self._present_index = 0

    # ---------------------------------------------------------------- helpers

    def _require(self, *roles):
        """Return an error dict if not logged in / wrong role, else None."""
        if self._current_user is None:
            return _err("Not signed in.")
        if roles and self._current_user["role"] not in roles:
            return _err("You don't have permission to do that.")
        return None

    def _owns_class(self, conn, class_id: int) -> bool:
        row = conn.execute(
            "SELECT teacher_id FROM classes WHERE id = ?", (class_id,)
        ).fetchone()
        return bool(row and row["teacher_id"] == self._current_user["id"])

    def _owns_lesson(self, conn, lesson_id: int) -> bool:
        row = conn.execute(
            "SELECT teacher_id FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        return bool(row and row["teacher_id"] == self._current_user["id"])

    # ------------------------------------------------------------- app state

    def get_app_state(self):
        """Called once by the UI on load to decide the first screen."""
        return {
            "ok": True,
            "setup_complete": db.setup_complete(),
            "user": self._current_user,
        }

    # ----------------------------------------------------------- setup wizard

    def complete_setup(self, school_name, location, admin_name, admin_username, admin_password):
        if db.setup_complete():
            return _err("Setup has already been completed on this device.")
        school_name = (school_name or "").strip()
        admin_username = (admin_username or "").strip()
        if not school_name:
            return _err("Enter the school name.")
        if not admin_name or not admin_username:
            return _err("Enter the administrator's name and username.")
        if len(admin_password or "") < 6:
            return _err("Choose a password of at least 6 characters.")

        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO settings (id, school_name, location, setup_complete) VALUES (1, ?, ?, 1)",
                (school_name, (location or "").strip()),
            )
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, 'admin')",
                (admin_username, auth.hash_password(admin_password), admin_name.strip()),
            )
            conn.commit()
            return {"ok": True}
        except Exception as e:
            return _err(f"Setup failed: {e}")
        finally:
            conn.close()

    # ------------------------------------------------------------------ auth

    def login(self, username, password):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND active = 1", ((username or "").strip(),)
            ).fetchone()
            if not row or not auth.verify_password(password or "", row["password_hash"]):
                return _err("Wrong username or password.")
            self._current_user = {
                "id": row["id"], "username": row["username"],
                "full_name": row["full_name"], "role": row["role"],
            }
            return {"ok": True, "user": self._current_user}
        finally:
            conn.close()

    def logout(self):
        self.stop_presentation()
        self._current_user = None
        return {"ok": True}

    # ----------------------------------------------------- settings & school

    def get_settings(self):
        conn = db.connect()
        try:
            row = conn.execute("SELECT school_name, location FROM settings WHERE id = 1").fetchone()
            return {"ok": True, "settings": dict(row) if row else None}
        finally:
            conn.close()

    def update_settings(self, school_name, location):
        e = self._require("admin")
        if e:
            return e
        if not (school_name or "").strip():
            return _err("Enter the school name.")
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE settings SET school_name = ?, location = ? WHERE id = 1",
                (school_name.strip(), (location or "").strip()),
            )
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    # ------------------------------------------------------- users (admin)

    def list_users(self):
        e = self._require("admin", "supervisor")
        if e:
            return e
        conn = db.connect()
        try:
            rows = conn.execute(
                "SELECT id, username, full_name, role, active, created_at FROM users ORDER BY role, full_name"
            ).fetchall()
            return {"ok": True, "users": [dict(r) for r in rows]}
        finally:
            conn.close()

    def create_user(self, full_name, username, password, role):
        e = self._require("admin")
        if e:
            return e
        if role not in ("teacher", "supervisor", "admin"):
            return _err("Choose a valid role.")
        if not (full_name or "").strip() or not (username or "").strip():
            return _err("Enter a full name and username.")
        if len(password or "") < 6:
            return _err("Choose a password of at least 6 characters.")
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                (username.strip(), auth.hash_password(password), full_name.strip(), role),
            )
            conn.commit()
            return {"ok": True}
        except Exception:
            return _err("That username is already taken.")
        finally:
            conn.close()

    def set_user_active(self, user_id, active):
        e = self._require("admin")
        if e:
            return e
        if user_id == self._current_user["id"]:
            return _err("You can't deactivate your own account.")
        conn = db.connect()
        try:
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def reset_user_password(self, user_id, new_password):
        e = self._require("admin")
        if e:
            return e
        if len(new_password or "") < 6:
            return _err("Choose a password of at least 6 characters.")
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (auth.hash_password(new_password), user_id),
            )
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    # ---------------------------------------------------------------- subjects

    def list_subjects(self):
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            rows = conn.execute("SELECT id, name FROM subjects ORDER BY name").fetchall()
            return {"ok": True, "subjects": [dict(r) for r in rows]}
        finally:
            conn.close()

    def create_subject(self, name):
        e = self._require("admin")
        if e:
            return e
        if not (name or "").strip():
            return _err("Enter a subject name.")
        conn = db.connect()
        try:
            conn.execute("INSERT INTO subjects (name) VALUES (?)", (name.strip(),))
            conn.commit()
            return {"ok": True}
        except Exception:
            return _err("That subject already exists.")
        finally:
            conn.close()

    # ---------------------------------------------------------------- classes

    def list_classes(self):
        """Teacher: own classes. Supervisor/Admin: all classes (read-only oversight)."""
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            if self._current_user["role"] == "teacher":
                rows = conn.execute(
                    """SELECT c.*, u.full_name AS teacher_name,
                              (SELECT COUNT(*) FROM lessons l WHERE l.class_id = c.id) AS lesson_count
                       FROM classes c JOIN users u ON u.id = c.teacher_id
                       WHERE c.teacher_id = ? ORDER BY c.name""",
                    (self._current_user["id"],),
                ).fetchall()
            else:  # admin, supervisor — view across all teachers
                rows = conn.execute(
                    """SELECT c.*, u.full_name AS teacher_name,
                              (SELECT COUNT(*) FROM lessons l WHERE l.class_id = c.id) AS lesson_count
                       FROM classes c JOIN users u ON u.id = c.teacher_id
                       ORDER BY u.full_name, c.name"""
                ).fetchall()
            return {"ok": True, "classes": [dict(r) for r in rows]}
        finally:
            conn.close()

    def create_class(self, name, grade=None):
        e = self._require("teacher")
        if e:
            return e
        if not (name or "").strip():
            return _err("Enter a class name.")
        if grade and grade not in db.GRADES:
            return _err("Choose a valid grade.")
        conn = db.connect()
        try:
            cur = conn.execute(
                "INSERT INTO classes (name, teacher_id, grade) VALUES (?, ?, ?)",
                (name.strip(), self._current_user["id"], grade or None),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid}
        finally:
            conn.close()

    def list_grades(self):
        e = self._require()
        if e:
            return e
        return {"ok": True, "grades": db.GRADES}

    def update_class(self, class_id, name, grade=None):
        e = self._require("teacher")
        if e:
            return e
        if not (name or "").strip():
            return _err("Enter a class name.")
        if grade and grade not in db.GRADES:
            return _err("Choose a valid grade.")
        conn = db.connect()
        try:
            if not self._owns_class(conn, class_id):
                return _err("You can only edit your own classes.")
            conn.execute(
                "UPDATE classes SET name = ?, grade = ? WHERE id = ?",
                (name.strip(), grade or None, class_id),
            )
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def delete_class(self, class_id):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_class(conn, class_id):
                return _err("You can only delete your own classes.")
            conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    # ---------------------------------------------------------------- lessons

    def list_lessons(self, class_id):
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            if self._current_user["role"] == "teacher" and not self._owns_class(conn, class_id):
                return _err("You can only view your own classes.")
            rows = conn.execute(
                """SELECT l.*, s.name AS subject_name,
                          (SELECT COUNT(*) FROM lesson_pages p WHERE p.lesson_id = l.id) AS page_count
                   FROM lessons l LEFT JOIN subjects s ON s.id = l.subject_id
                   WHERE l.class_id = ? ORDER BY l.updated_at DESC""",
                (class_id,),
            ).fetchall()
            cls = conn.execute(
                "SELECT c.*, u.full_name AS teacher_name FROM classes c JOIN users u ON u.id = c.teacher_id WHERE c.id = ?",
                (class_id,),
            ).fetchone()
            return {
                "ok": True,
                "lessons": [dict(r) for r in rows],
                "class": dict(cls) if cls else None,
            }
        finally:
            conn.close()

    def create_lesson(self, class_id, title, subject_id):
        e = self._require("teacher")
        if e:
            return e
        if not (title or "").strip():
            return _err("Enter a lesson title.")
        conn = db.connect()
        try:
            if not self._owns_class(conn, class_id):
                return _err("You can only add lessons to your own classes.")
            cur = conn.execute(
                "INSERT INTO lessons (class_id, subject_id, teacher_id, title) VALUES (?, ?, ?, ?)",
                (class_id, subject_id or None, self._current_user["id"], title.strip()),
            )
            lesson_id = cur.lastrowid
            conn.execute(
                "INSERT INTO lesson_pages (lesson_id, page_number, title) VALUES (?, 1, 'Page 1')",
                (lesson_id,),
            )
            conn.commit()
            return {"ok": True, "id": lesson_id}
        finally:
            conn.close()

    def delete_lesson(self, lesson_id):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_lesson(conn, lesson_id):
                return _err("You can only delete your own lessons.")
            conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def get_lesson(self, lesson_id):
        """Full lesson with ordered pages — used by editor, viewer, presenter."""
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            lesson = conn.execute(
                """SELECT l.*, s.name AS subject_name, c.name AS class_name, u.full_name AS teacher_name
                   FROM lessons l
                   LEFT JOIN subjects s ON s.id = l.subject_id
                   JOIN classes c ON c.id = l.class_id
                   JOIN users u ON u.id = l.teacher_id
                   WHERE l.id = ?""",
                (lesson_id,),
            ).fetchone()
            if not lesson:
                return _err("Lesson not found.")
            if (self._current_user["role"] == "teacher"
                    and lesson["teacher_id"] != self._current_user["id"]):
                return _err("You can only open your own lessons.")
            pages = conn.execute(
                "SELECT * FROM lesson_pages WHERE lesson_id = ? ORDER BY page_number",
                (lesson_id,),
            ).fetchall()
            return {"ok": True, "lesson": dict(lesson), "pages": [dict(p) for p in pages]}
        finally:
            conn.close()

    # ------------------------------------------------------------------ pages

    def save_page(self, page_id, title, content_html):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT lesson_id FROM lesson_pages WHERE id = ?", (page_id,)
            ).fetchone()
            if not row or not self._owns_lesson(conn, row["lesson_id"]):
                return _err("You can only edit your own lessons.")
            conn.execute(
                "UPDATE lesson_pages SET title = ?, content_html = ? WHERE id = ?",
                ((title or "").strip(), content_html or "", page_id),
            )
            conn.execute(
                "UPDATE lessons SET updated_at = datetime('now') WHERE id = ?",
                (row["lesson_id"],),
            )
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def add_page(self, lesson_id):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_lesson(conn, lesson_id):
                return _err("You can only edit your own lessons.")
            n = conn.execute(
                "SELECT COALESCE(MAX(page_number), 0) + 1 FROM lesson_pages WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO lesson_pages (lesson_id, page_number, title) VALUES (?, ?, ?)",
                (lesson_id, n, f"Page {n}"),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid, "page_number": n}
        finally:
            conn.close()

    def delete_page(self, page_id):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT lesson_id FROM lesson_pages WHERE id = ?", (page_id,)
            ).fetchone()
            if not row or not self._owns_lesson(conn, row["lesson_id"]):
                return _err("You can only edit your own lessons.")
            count = conn.execute(
                "SELECT COUNT(*) FROM lesson_pages WHERE lesson_id = ?", (row["lesson_id"],)
            ).fetchone()[0]
            if count <= 1:
                return _err("A lesson needs at least one page.")
            conn.execute("DELETE FROM lesson_pages WHERE id = ?", (page_id,))
            # Renumber to keep page_number contiguous
            pages = conn.execute(
                "SELECT id FROM lesson_pages WHERE lesson_id = ? ORDER BY page_number",
                (row["lesson_id"],),
            ).fetchall()
            for i, p in enumerate(pages, start=1):
                conn.execute("UPDATE lesson_pages SET page_number = ? WHERE id = ?", (i, p["id"]))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def reorder_pages(self, lesson_id, ordered_page_ids):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_lesson(conn, lesson_id):
                return _err("You can only edit your own lessons.")
            for i, pid in enumerate(ordered_page_ids, start=1):
                conn.execute(
                    "UPDATE lesson_pages SET page_number = ? WHERE id = ? AND lesson_id = ?",
                    (i, pid, lesson_id),
                )
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()
            
    # ------------------------------------------------------- AI generation

    def ai_get_status(self):
        e = self._require()
        if e:
            return e
        from . import ai_generator
        return {"ok": True, **ai_generator.get_ai_status()}

    def ai_generate_and_create(self, class_id, kind, subject_id, topic, count=10, document_ids=None):
        """Generate a Lesson or a Test from the Classes screen and create the
        real record directly — replaces the old lesson-editor 'Generate with
        AI' button, which only pasted HTML into whatever page was open.
        document_ids: documents the teacher explicitly picked (from the ones
        assigned to this class's grade + the chosen subject) to ground
        generation in, instead of relying on automatic keyword matching."""
        e = self._require("teacher")
        if e:
            return e
        if kind not in ("lesson", "test"):
            return _err("Choose Lesson or Test.")
        if not (topic or "").strip():
            return _err("Enter a topic.")
        conn = db.connect()
        try:
            if not self._owns_class(conn, class_id):
                return _err("You can only generate content for your own classes.")
            cls = conn.execute("SELECT grade FROM classes WHERE id = ?", (class_id,)).fetchone()
        finally:
            conn.close()
        grade = cls["grade"] if cls else None
        document_ids = [int(d) for d in document_ids] if document_ids else None

        from . import ai_generator, ai_html_formatter
        try:
            if kind == "lesson":
                result = ai_generator.generate_lesson_draft(topic, grade, subject_id, document_ids=document_ids)
                if not (result["content"] or "").strip():
                    return _err("AI generation did not return any content. Try a different topic.")
                pages = ai_html_formatter.lesson_plan_text_to_pages(result["content"])
                new_id = self._create_lesson_with_pages(class_id, topic.strip(), subject_id, pages)
                return {"ok": True, "kind": "lesson", "id": new_id}
            else:
                result = ai_generator.generate_quiz(topic, int(count), grade, subject_id, document_ids=document_ids)
                questions = ai_generator.parse_quiz_to_questions(result["content"])
                if not questions:
                    return _err("AI generation did not return any usable questions. Try a different topic.")
                new_id = self._create_test_with_questions(class_id, topic.strip(), subject_id, questions)
                return {"ok": True, "kind": "test", "id": new_id, "meta": result["meta"]}
        except ai_generator.AIUnavailable as ex:
            return _err(str(ex))

    def _create_lesson_with_pages(self, class_id, title, subject_id, pages):
        """pages: list of {"title", "content_html"} — one lesson_pages row per
        entry, in order. Falls back to a single blank page if generation
        produced none, so a lesson is never left with zero pages."""
        conn = db.connect()
        try:
            cur = conn.execute(
                "INSERT INTO lessons (class_id, subject_id, teacher_id, title) VALUES (?, ?, ?, ?)",
                (class_id, subject_id or None, self._current_user["id"], title),
            )
            lesson_id = cur.lastrowid
            for i, p in enumerate(pages or [{"title": "Page 1", "content_html": ""}], start=1):
                conn.execute(
                    "INSERT INTO lesson_pages (lesson_id, page_number, title, content_html) VALUES (?, ?, ?, ?)",
                    (lesson_id, i, p["title"], p["content_html"]),
                )
            conn.commit()
            return lesson_id
        finally:
            conn.close()

    def _create_test_with_questions(self, class_id, title, subject_id, questions):
        conn = db.connect()
        try:
            cur = conn.execute(
                "INSERT INTO tests (class_id, subject_id, teacher_id, title) VALUES (?, ?, ?, ?)",
                (class_id, subject_id or None, self._current_user["id"], title),
            )
            test_id = cur.lastrowid
            for i, q in enumerate(questions, start=1):
                conn.execute(
                    """INSERT INTO test_questions
                       (test_id, question_number, kind, prompt, options_json, answer, explanation, marks)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (test_id, i, q["kind"], q["prompt"], json.dumps(q["options"]),
                     q["answer"], q["explanation"], q["marks"]),
                )
            conn.commit()
            return test_id
        finally:
            conn.close()

    # -------------------------------------------------------- Library documents (admin upload, teacher-read)

    def list_documents(self, grade=None, subject_id=None):
        e = self._require()
        if e:
            return e
        from . import documents
        return {"ok": True, "documents": documents.list_documents(grade, subject_id)}

    def upload_document(self, title, subject_id, grade):
        e = self._require("admin")
        if e:
            return e
        if not (title or "").strip():
            return _err("Enter a document title.")
        if grade not in db.GRADES:
            return _err("Choose a valid grade.")
        if self._main_window is None:
            return _err("File dialog unavailable.")
        fd = getattr(webview, "FileDialog", None)
        dialog_type = fd.OPEN if fd else webview.OPEN_DIALOG
        picked = self._main_window.create_file_dialog(
            dialog_type, allow_multiple=False,
            file_types=("Documents (*.pdf;*.docx)", "All files (*.*)"),
        )
        if not picked:
            return {"ok": True, "cancelled": True}
        src_path = picked[0] if isinstance(picked, (list, tuple)) else picked
        if not os.path.isfile(src_path):
            return _err("That file could not be read.")
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in (".pdf", ".docx"):
            return _err("Use a PDF or Word (.docx) file.")
        from . import documents
        return documents.store_document(src_path, title.strip(), subject_id or None, grade)

    def delete_document(self, document_id):
        e = self._require("admin")
        if e:
            return e
        from . import documents
        return documents.delete_document(document_id)

    # -------------------------------------------------------- AI settings (admin)

    def get_ai_settings(self):
        e = self._require("admin")
        if e:
            return e
        from . import ai_settings
        s = ai_settings.get_ai_settings()
        s["api_key_set"] = bool(s.pop("api_key"))  # never send the raw key back to the UI
        return {"ok": True, "settings": s}

    def update_ai_settings(self, provider, api_key, model_filename):
        e = self._require("admin")
        if e:
            return e
        from . import ai_settings
        ai_settings.set_ai_settings(provider=provider, api_key=api_key, model_filename=model_filename)
        return {"ok": True}        

    # ------------------------------------------------------------------ media

    def attach_video(self, lesson_id):
        """Native file picker -> copy the video into the app's media folder ->
        return a file:// URI for embedding. Copying (not referencing in place)
        means the lesson keeps working after a USB stick is unplugged."""
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_lesson(conn, lesson_id):
                return _err("You can only edit your own lessons.")
        finally:
            conn.close()

        win = self._main_window
        if win is None:
            return _err("File dialog unavailable.")
        # pywebview 6 uses webview.FileDialog.OPEN; 5.x used webview.OPEN_DIALOG
        fd = getattr(webview, "FileDialog", None)
        dialog_type = fd.OPEN if fd else webview.OPEN_DIALOG
        picked = win.create_file_dialog(
            dialog_type,
            allow_multiple=False,
            file_types=("Video files (*.mp4;*.webm;*.m4v)", "All files (*.*)"),
        )
        if not picked:
            return {"ok": True, "cancelled": True}

        src_path = picked[0] if isinstance(picked, (list, tuple)) else picked
        if not os.path.isfile(src_path):
            return _err("That file could not be read.")
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in (".mp4", ".webm", ".m4v"):
            return _err("Use an .mp4, .webm or .m4v file — other formats may not play.")

        safe_name = f"{int(time.time())}_{os.path.basename(src_path)}"
        dest = os.path.join(db.data_dir(), "media", safe_name)
        try:
            shutil.copy2(src_path, dest)
        except OSError as ex:
            return _err(f"Could not copy the video: {ex}")

        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO media (filename, stored_path, lesson_id) VALUES (?, ?, ?)",
                (os.path.basename(src_path), dest, lesson_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "filename": safe_name,
                "src": media_server.base_url() + urllib.parse.quote(safe_name),
                "size_mb": round(os.path.getsize(dest) / 1_048_576, 1)}

    def get_media_base(self):
        """Current base URL for lesson media. The port is ephemeral, so the UI
        asks for this at startup and rebuilds video srcs from data-filename."""
        return {"ok": True, "base": media_server.base_url()}

    # ------------------------------------------------------- simulation library

    PHET_API = "https://phet-api.colorado.edu/partner-services/2.0/metadata/simulations?locale=en"
    PHET_HOST = "https://phet.colorado.edu"

    def list_sims(self):
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            rows = conn.execute("SELECT * FROM sims ORDER BY title").fetchall()
            return {"ok": True, "sims": [dict(r) for r in rows],
                    "ggb_ready": media_server.find_ggb_runtime() is not None,
                    "ggb_base": media_server.ggb_base_url()}
        finally:
            conn.close()

    # ------------------------------------------------------- GeoGebra runtime

    def geogebra_status(self):
        e = self._require()
        if e:
            return e
        return {"ok": True, "installed": media_server.find_ggb_runtime() is not None}

    def install_geogebra_runtime(self):
        """Admin picks the official GeoGebra Math Apps Bundle zip; we extract
        it into the media tree so .ggb lesson pages become playable offline."""
        e = self._require("admin")
        if e:
            return e
        if self._main_window is None:
            return _err("File dialog unavailable.")
        fd = getattr(webview, "FileDialog", None)
        dialog_type = fd.OPEN if fd else webview.OPEN_DIALOG
        picked = self._main_window.create_file_dialog(
            dialog_type, allow_multiple=False,
            file_types=("GeoGebra bundle (*.zip)", "All files (*.*)"),
        )
        if not picked:
            return {"ok": True, "cancelled": True}
        zip_path = picked[0] if isinstance(picked, (list, tuple)) else picked
        return self._extract_ggb_runtime(zip_path)

    def _extract_ggb_runtime(self, zip_path):
        if not os.path.isfile(zip_path):
            return _err("That file could not be read.")
        dest_root = os.path.join(db.data_dir(), "media", media_server.GGB_RUNTIME_DIR)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                if not any(n.endswith("deployggb.js") for n in names):
                    return _err("That zip doesn't look like the GeoGebra Math Apps Bundle — deployggb.js is missing.")
                # guard against zip path traversal before touching the disk
                dest_abs = os.path.abspath(dest_root)
                for n in names:
                    target = os.path.abspath(os.path.join(dest_abs, n))
                    if not target.startswith(dest_abs + os.sep) and target != dest_abs:
                        return _err("That zip contains unsafe paths and was rejected.")
                if os.path.isdir(dest_root):
                    shutil.rmtree(dest_root, ignore_errors=True)
                os.makedirs(dest_root, exist_ok=True)
                zf.extractall(dest_root)
        except zipfile.BadZipFile:
            return _err("That file isn't a valid zip archive.")
        except OSError as ex:
            return _err(f"Could not install the runtime: {ex}")
        if media_server.find_ggb_runtime() is None:
            return _err("The zip was extracted but the runtime couldn't be located inside it.")
        return {"ok": True}

    def install_sim_file(self):
        """Admin installs a simulation from a local file (offline path)."""
        e = self._require("admin")
        if e:
            return e
        if self._main_window is None:
            return _err("File dialog unavailable.")
        fd = getattr(webview, "FileDialog", None)
        dialog_type = fd.OPEN if fd else webview.OPEN_DIALOG
        picked = self._main_window.create_file_dialog(
            dialog_type, allow_multiple=False,
            file_types=("Simulations (*.html;*.htm;*.ggb)", "All files (*.*)"),
        )
        if not picked:
            return {"ok": True, "cancelled": True}
        src_path = picked[0] if isinstance(picked, (list, tuple)) else picked
        if not os.path.isfile(src_path):
            return _err("That file could not be read.")
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in (".html", ".htm", ".ggb"):
            return _err("Use a PhET .html file or a GeoGebra .ggb file.")
        kind = "ggb" if ext == ".ggb" else "html"
        title = os.path.splitext(os.path.basename(src_path))[0].replace("-", " ").replace("_", " ").strip()
        return self._store_sim(src_path, title, kind, source="local file")

    def _store_sim(self, src_path, title, kind, source):
        safe_name = f"sim_{int(time.time())}_{os.path.basename(src_path)}"
        dest = os.path.join(db.data_dir(), "media", safe_name)
        try:
            shutil.copy2(src_path, dest)
        except OSError as ex:
            return _err(f"Could not copy the simulation: {ex}")
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO sims (title, kind, filename, source) VALUES (?, ?, ?, ?)",
                (title, kind, safe_name, source),
            )
            conn.commit()
            return {"ok": True, "title": title}
        finally:
            conn.close()

    def delete_sim(self, sim_id):
        e = self._require("admin")
        if e:
            return e
        conn = db.connect()
        try:
            sim = conn.execute("SELECT * FROM sims WHERE id = ?", (sim_id,)).fetchone()
            if not sim:
                return _err("Simulation not found.")
            used = conn.execute(
                "SELECT COUNT(*) FROM lesson_pages WHERE sim_path = ?", (sim["filename"],)
            ).fetchone()[0]
            if used:
                return _err(f"This simulation is used by {used} lesson page(s). Remove those pages first.")
            conn.execute("DELETE FROM sims WHERE id = ?", (sim_id,))
            conn.commit()
        finally:
            conn.close()
        try:
            os.remove(os.path.join(db.data_dir(), "media", sim["filename"]))
        except OSError:
            pass
        return {"ok": True}

    def phet_catalog(self):
        """Fetch the PhET catalog (needs the 4G dongle connected).
        NOTE: production use of this metadata service is covered by the
        organisation's license agreement with PhET."""
        e = self._require("admin")
        if e:
            return e
        try:
            req = urllib.request.Request(self.PHET_API, headers={"User-Agent": "NhavaLearn-Desktop"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
        except Exception:
            return _err("Could not reach the PhET catalog. Check that the internet dongle is connected and try again.")
        sims = []
        for s in data.get("simulations", []):
            d = s.get("defaultData") or {}
            run_url = d.get("runUrl") or ""
            if run_url.endswith(".html"):
                sims.append({"title": d.get("title") or s.get("name", ""),
                             "name": s.get("name", ""), "run_url": run_url})
        sims.sort(key=lambda x: x["title"].lower())
        return {"ok": True, "sims": sims, "count": len(sims)}

    def phet_download(self, title, run_url):
        """Download one PhET sim (a self-contained single HTML file) into the library."""
        e = self._require("admin")
        if e:
            return e
        # only accept paths shaped like PhET sim URLs — never fetch arbitrary URLs
        if not (isinstance(run_url, str) and run_url.startswith("/sims/html/") and run_url.endswith(".html")):
            return _err("That doesn't look like a valid PhET simulation.")
        conn = db.connect()
        try:
            dup = conn.execute("SELECT 1 FROM sims WHERE source = ?", (run_url,)).fetchone()
            if dup:
                return _err("That simulation is already installed.")
        finally:
            conn.close()
        tmp_path = os.path.join(db.data_dir(), "media", "_phet_download.tmp")
        try:
            req = urllib.request.Request(self.PHET_HOST + run_url,
                                         headers={"User-Agent": "NhavaLearn-Desktop"})
            with urllib.request.urlopen(req, timeout=180) as resp, open(tmp_path, "wb") as f:
                shutil.copyfileobj(resp, f)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return _err(f"Download failed for \u201c{title}\u201d. Check the internet connection and try again.")
        # store under the sim's own filename, then remove the temp copy
        result = self._store_sim(tmp_path, title, "phet", source=run_url)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return result

    def add_sim_page(self, lesson_id, sim_id):
        """Teacher adds an installed simulation as a lesson page."""
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_lesson(conn, lesson_id):
                return _err("You can only edit your own lessons.")
            sim = conn.execute("SELECT * FROM sims WHERE id = ?", (sim_id,)).fetchone()
            if not sim:
                return _err("Simulation not found — ask the admin to install it.")
            n = conn.execute(
                "SELECT COALESCE(MAX(page_number), 0) + 1 FROM lesson_pages WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()[0]
            cur = conn.execute(
                """INSERT INTO lesson_pages (lesson_id, page_number, title, page_type, sim_path)
                   VALUES (?, ?, ?, 'simulation', ?)""",
                (lesson_id, n, sim["title"], sim["filename"]),
            )
            conn.execute("UPDATE lessons SET updated_at = datetime('now') WHERE id = ?", (lesson_id,))
            conn.commit()
            return {"ok": True, "id": cur.lastrowid, "page_number": n,
                    "title": sim["title"], "sim_path": sim["filename"],
                    "kind": sim["kind"]}
        finally:
            conn.close()

    # ------------------------------------------------------------------ tests

    QUESTION_KINDS = ("multiple_choice", "true_false", "short_answer", "long_answer")

    def _owns_test(self, conn, test_id: int) -> bool:
        row = conn.execute("SELECT teacher_id FROM tests WHERE id = ?", (test_id,)).fetchone()
        return bool(row and row["teacher_id"] == self._current_user["id"])

    def list_tests(self, class_id):
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            if self._current_user["role"] == "teacher" and not self._owns_class(conn, class_id):
                return _err("You can only view your own classes.")
            rows = conn.execute(
                """SELECT t.*, s.name AS subject_name,
                          (SELECT COUNT(*) FROM test_questions q WHERE q.test_id = t.id) AS question_count,
                          (SELECT COALESCE(SUM(q.marks), 0) FROM test_questions q WHERE q.test_id = t.id) AS total_marks
                   FROM tests t LEFT JOIN subjects s ON s.id = t.subject_id
                   WHERE t.class_id = ? ORDER BY t.updated_at DESC""",
                (class_id,),
            ).fetchall()
            return {"ok": True, "tests": [dict(r) for r in rows]}
        finally:
            conn.close()

    def create_test(self, class_id, title, subject_id):
        e = self._require("teacher")
        if e:
            return e
        if not (title or "").strip():
            return _err("Enter a test title.")
        conn = db.connect()
        try:
            if not self._owns_class(conn, class_id):
                return _err("You can only add tests to your own classes.")
            cur = conn.execute(
                "INSERT INTO tests (class_id, subject_id, teacher_id, title) VALUES (?, ?, ?, ?)",
                (class_id, subject_id or None, self._current_user["id"], title.strip()),
            )
            test_id = cur.lastrowid
            conn.execute(
                "INSERT INTO test_questions (test_id, question_number) VALUES (?, 1)",
                (test_id,),
            )
            conn.commit()
            return {"ok": True, "id": test_id}
        finally:
            conn.close()

    def update_test(self, test_id, title, instructions, layout=None):
        e = self._require("teacher")
        if e:
            return e
        if not (title or "").strip():
            return _err("Enter a test title.")
        conn = db.connect()
        try:
            if not self._owns_test(conn, test_id):
                return _err("You can only edit your own tests.")
            conn.execute(
                "UPDATE tests SET title = ?, instructions = ?, updated_at = datetime('now') WHERE id = ?",
                (title.strip(), (instructions or "").strip(), test_id),
            )
            if layout in ("pages", "single"):
                conn.execute("UPDATE tests SET layout = ? WHERE id = ?", (layout, test_id))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def delete_test(self, test_id):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_test(conn, test_id):
                return _err("You can only delete your own tests.")
            conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def get_test(self, test_id):
        """Test with ordered questions. Teachers see only their own; supervisor
        and admin can read any (answer keys included — that's their role)."""
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            test = conn.execute(
                """SELECT t.*, s.name AS subject_name, c.name AS class_name, u.full_name AS teacher_name
                   FROM tests t
                   LEFT JOIN subjects s ON s.id = t.subject_id
                   JOIN classes c ON c.id = t.class_id
                   JOIN users u ON u.id = t.teacher_id
                   WHERE t.id = ?""",
                (test_id,),
            ).fetchone()
            if not test:
                return _err("Test not found.")
            if (self._current_user["role"] == "teacher"
                    and test["teacher_id"] != self._current_user["id"]):
                return _err("You can only open your own tests.")
            rows = conn.execute(
                "SELECT * FROM test_questions WHERE test_id = ? ORDER BY question_number",
                (test_id,),
            ).fetchall()
            questions = []
            for r in rows:
                q = dict(r)
                try:
                    q["options"] = json.loads(q.pop("options_json") or "[]")
                except ValueError:
                    q["options"] = []
                questions.append(q)
            return {"ok": True, "test": dict(test), "questions": questions}
        finally:
            conn.close()

    def add_question(self, test_id, kind):
        e = self._require("teacher")
        if e:
            return e
        if kind not in self.QUESTION_KINDS:
            return _err("Choose a valid question type.")
        conn = db.connect()
        try:
            if not self._owns_test(conn, test_id):
                return _err("You can only edit your own tests.")
            n = conn.execute(
                "SELECT COALESCE(MAX(question_number), 0) + 1 FROM test_questions WHERE test_id = ?",
                (test_id,),
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO test_questions (test_id, question_number, kind) VALUES (?, ?, ?)",
                (test_id, n, kind),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid, "question_number": n, "kind": kind}
        finally:
            conn.close()

    def save_question(self, question_id, kind, prompt, options, answer, explanation, marks):
        e = self._require("teacher")
        if e:
            return e
        if kind not in self.QUESTION_KINDS:
            return _err("Choose a valid question type.")
        try:
            marks = max(0, int(marks))
        except (TypeError, ValueError):
            marks = 1
        if not isinstance(options, list):
            options = []
        options = [str(o) for o in options]
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT test_id FROM test_questions WHERE id = ?", (question_id,)
            ).fetchone()
            if not row or not self._owns_test(conn, row["test_id"]):
                return _err("You can only edit your own tests.")
            conn.execute(
                """UPDATE test_questions
                   SET kind = ?, prompt = ?, options_json = ?, answer = ?, explanation = ?, marks = ?
                   WHERE id = ?""",
                (kind, (prompt or "").strip(), json.dumps(options),
                 str(answer or "").strip(), (explanation or "").strip(), marks, question_id),
            )
            conn.execute("UPDATE tests SET updated_at = datetime('now') WHERE id = ?", (row["test_id"],))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def delete_question(self, question_id):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT test_id FROM test_questions WHERE id = ?", (question_id,)
            ).fetchone()
            if not row or not self._owns_test(conn, row["test_id"]):
                return _err("You can only edit your own tests.")
            count = conn.execute(
                "SELECT COUNT(*) FROM test_questions WHERE test_id = ?", (row["test_id"],)
            ).fetchone()[0]
            if count <= 1:
                return _err("A test needs at least one question.")
            conn.execute("DELETE FROM test_questions WHERE id = ?", (question_id,))
            qs = conn.execute(
                "SELECT id FROM test_questions WHERE test_id = ? ORDER BY question_number",
                (row["test_id"],),
            ).fetchall()
            for i, q in enumerate(qs, start=1):
                conn.execute("UPDATE test_questions SET question_number = ? WHERE id = ?", (i, q["id"]))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    def reorder_questions(self, test_id, ordered_question_ids):
        e = self._require("teacher")
        if e:
            return e
        conn = db.connect()
        try:
            if not self._owns_test(conn, test_id):
                return _err("You can only edit your own tests.")
            for i, qid in enumerate(ordered_question_ids, start=1):
                conn.execute(
                    "UPDATE test_questions SET question_number = ? WHERE id = ? AND test_id = ?",
                    (i, qid, test_id),
                )
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    # ---------------------------------------------- test page generation

    @staticmethod
    def _esc(s) -> str:
        return html.escape(str(s or "")).replace("\n", "<br>")

    def _question_html(self, q, reveal: bool) -> str:
        esc = self._esc
        marks = int(q.get("marks") or 0)
        parts = [f'<div style="color:#64748B;font-size:.6em;font-weight:700">'
                 f'[{marks} mark{"" if marks == 1 else "s"}]</div>']
        parts.append(f'<div style="margin:0.4em 0 0.8em">{esc(q.get("prompt"))}</div>')
        kind = q.get("kind")
        answer = str(q.get("answer") or "")

        if kind == "multiple_choice":
            options = q.get("options") or []
            try:
                correct = int(answer)
            except ValueError:
                correct = -1
            for idx, opt in enumerate(options):
                letter = chr(65 + idx)
                hit = reveal and idx == correct
                style = ("color:#16A34A;font-weight:700" if hit else "")
                tick = ' <span style="color:#16A34A">&#10003;</span>' if hit else ""
                parts.append(
                    f'<div style="display:flex;gap:0.6em;margin:0.35em 0;{style}">'
                    f'<b>{letter}.</b><span>{esc(opt)}{tick}</span></div>')
        elif kind == "true_false":
            parts.append('<div style="margin:0.5em 0;color:#334155"><b>TRUE</b>'
                         '&nbsp;&nbsp;/&nbsp;&nbsp;<b>FALSE</b></div>')
            if reveal:
                verdict = "TRUE" if answer.lower() in ("true", "1", "yes") else "FALSE"
                parts.append(f'<div style="color:#16A34A;font-weight:700">Answer: {verdict}</div>')
        elif kind == "short_answer":
            if not reveal:
                parts.append('<div style="color:#94A3B8;margin-top:0.8em">'
                             'Answer: ' + "&#8230;" * 14 + "</div>")
        else:  # long_answer
            if not reveal:
                parts.append('<div style="color:#94A3B8;margin-top:0.8em;font-size:.7em">'
                             'Answer fully in your exercise book.</div>')

        if reveal:
            if kind in ("short_answer", "long_answer") and answer:
                parts.append(
                    '<div style="margin-top:0.9em;padding:0.5em 0.8em;border-left:6px solid #16A34A;'
                    'background:#F0FDF4;border-radius:8px">'
                    f'<b style="color:#16A34A">Answer:</b> {esc(answer)}</div>')
            if q.get("explanation"):
                parts.append(
                    '<div style="margin-top:0.6em;padding:0.5em 0.8em;border-left:6px solid #2563EB;'
                    'background:#EFF6FF;border-radius:8px;font-size:.75em">'
                    f'<b style="color:#2563EB">Why:</b> {esc(q.get("explanation"))}</div>')
        return "".join(parts)

    def test_pages(self, test_id, reveal_answers=False):
        """Turn a test into presentation-shaped pages: cover + one per question.
        Reused by the projector AND the in-app read-only viewer."""
        result = self.get_test(test_id)
        if not result["ok"]:
            return result
        t, questions = result["test"], result["questions"]
        reveal = bool(reveal_answers)
        esc = self._esc
        total = sum(int(q.get("marks") or 0) for q in questions)
        cover = (
            f'<div style="color:#64748B;font-size:.6em;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.05em">{esc(t.get("subject_name") or "Test")}'
            f'{" — answers revealed" if reveal else ""}</div>'
            f'<div style="margin:0.5em 0">{esc(t.get("instructions")) or "Answer all questions."}</div>'
            f'<div style="color:#334155;font-size:.75em"><b>{len(questions)}</b> question'
            f'{"" if len(questions) == 1 else "s"} &nbsp;&middot;&nbsp; <b>{total}</b> marks total</div>'
        )
        if (t.get("layout") or "pages") == "single":
            # One-pager: the whole test on a single scrollable page
            blocks = [cover]
            for i, q in enumerate(questions, start=1):
                blocks.append(
                    f'<hr style="border:none;border-top:2px solid #E2E8F0;margin:1em 0">'
                    f'<div style="font-weight:800;color:#2563EB;font-size:.8em">Question {i}</div>'
                    + self._question_html(q, reveal)
                )
            pages = [{"title": t["title"], "content_html": "".join(blocks),
                      "page_type": "content"}]
        else:
            pages = [{"title": t["title"], "content_html": cover, "page_type": "content"}]
            for i, q in enumerate(questions, start=1):
                pages.append({"title": f"Question {i}",
                              "content_html": self._question_html(q, reveal),
                              "page_type": "content"})
        return {"ok": True, "pages": pages, "test": t}

    def start_test_presentation(self, test_id, reveal_answers=False):
        e = self._require("teacher", "admin", "supervisor")
        if e:
            return e
        result = self.test_pages(test_id, reveal_answers)
        if not result["ok"]:
            return result
        return self._open_presentation(result["pages"])

    # ------------------------------------------------------------- presenting

    def start_presentation(self, lesson_id):
        """Open a clean fullscreen window on the projector's extended display.

        Projector and laptop are the same machine over HDMI, so this is plain
        Windows multi-monitor — no sync protocol, no PINs, no network.
        """
        e = self._require("teacher", "admin", "supervisor")
        if e:
            return e
        result = self.get_lesson(lesson_id)
        if not result["ok"]:
            return result
        return self._open_presentation(result["pages"])

    def _open_presentation(self, pages):
        """Shared by lesson and test presenting: same window, same controls."""
        self.stop_presentation()
        self._present_pages = pages
        self._present_index = 0

        screens = webview.screens
        target = screens[1] if len(screens) > 1 else screens[0]
        self._present_window = webview.create_window(
            title="NhavaLearn — Presentation",
            url=resource_path(os.path.join("ui", "present.html")),
            screen=target,
            fullscreen=True,
            js_api=self,
        )
        self._present_window.events.closed += self._on_present_closed
        return {
            "ok": True,
            "page_count": len(pages),
            "external_display": len(screens) > 1,
        }

    def _on_present_closed(self):
        """Fires however the window dies (Esc, Alt+F4, stop button)."""
        self._present_window = None
        self._present_pages = []
        self._notify_main("presenterEnded()")

    def _notify_main(self, js: str):
        if self._main_window:
            try:
                self._main_window.evaluate_js(js)
            except Exception:
                pass

    def get_presentation(self):
        """Called by the presentation window itself once it has loaded."""
        return {"ok": True, "pages": self._present_pages, "index": self._present_index}

    def present_goto(self, page_index):
        """Teacher's control bar -> presentation window."""
        if not self._present_window:
            return _err("No presentation is running.")
        try:
            self._present_window.evaluate_js(f"showPage({int(page_index)})")
            return {"ok": True}
        except Exception as ex:
            return _err(f"Could not change page: {ex}")

    def present_report(self, page_index):
        """Presentation window -> teacher's control bar. Called after the
        presentation navigates itself (arrow keys / touch arrows at the board),
        so the bar on the laptop stays in step."""
        self._present_index = int(page_index)
        self._notify_main(f"presenterSync({self._present_index})")
        return {"ok": True}

    def present_scroll(self, direction):
        """Scroll the presentation content from the teacher's control bar —
        needed for one-pager tests and long lesson pages."""
        if not self._present_window:
            return _err("No presentation is running.")
        try:
            self._present_window.evaluate_js(f"scrollContent({int(direction)})")
            return {"ok": True}
        except Exception as ex:
            return _err(f"Could not scroll: {ex}")

    def present_close_request(self):
        """Esc pressed inside the presentation window."""
        return self.stop_presentation()

    def stop_presentation(self):
        if self._present_window:
            w, self._present_window = self._present_window, None
            self._present_pages = []
            try:
                w.destroy()   # triggers _on_present_closed -> presenterEnded()
            except Exception:
                self._notify_main("presenterEnded()")
        return {"ok": True}

    # ------------------------------------------------------------- dashboard

    def get_dashboard(self):
        e = self._require()
        if e:
            return e
        conn = db.connect()
        try:
            uid = self._current_user["id"]
            role = self._current_user["role"]
            if role == "teacher":
                stats = {
                    "classes": conn.execute(
                        "SELECT COUNT(*) FROM classes WHERE teacher_id = ?", (uid,)
                    ).fetchone()[0],
                    "lessons": conn.execute(
                        "SELECT COUNT(*) FROM lessons WHERE teacher_id = ?", (uid,)
                    ).fetchone()[0],
                    "tests": conn.execute(
                        "SELECT COUNT(*) FROM tests WHERE teacher_id = ?", (uid,)
                    ).fetchone()[0],
                }
            else:
                stats = {
                    "teachers": conn.execute(
                        "SELECT COUNT(*) FROM users WHERE role = 'teacher' AND active = 1"
                    ).fetchone()[0],
                    "classes": conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0],
                    "lessons": conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0],
                }
            school = conn.execute("SELECT school_name FROM settings WHERE id = 1").fetchone()
            return {
                "ok": True,
                "stats": stats,
                "school_name": school["school_name"] if school else "",
            }
        finally:
            conn.close()
