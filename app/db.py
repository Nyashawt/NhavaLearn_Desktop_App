"""SQLite storage for NhavaLearn Desktop.

One file on disk, zero configuration, no server process. The database lives
in a per-machine data directory so reinstalling the app never touches data.
"""
import os
import sqlite3

APP_NAME = "NhavaLearn"


def data_dir() -> str:
    """Per-machine writable data directory (Windows: %LOCALAPPDATA%\\NhavaLearn)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, APP_NAME)
    os.makedirs(os.path.join(path, "media"), exist_ok=True)
    return path


def db_path() -> str:
    return os.path.join(data_dir(), "nhavalearn.db")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # safer against power loss (solar setups)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    school_name    TEXT NOT NULL,
    location       TEXT,
    setup_complete INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'supervisor')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subjects (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS classes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lessons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lesson_pages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id    INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    page_number  INTEGER NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    page_type    TEXT NOT NULL DEFAULT 'content' CHECK (page_type IN ('content', 'simulation')),
    content_html TEXT NOT NULL DEFAULT '',
    sim_path     TEXT,
    presentable  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    lesson_id   INTEGER REFERENCES lessons(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sims (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('phet', 'ggb', 'html')),
    filename   TEXT NOT NULL UNIQUE,
    source     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id     INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    subject_id   INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    teacher_id   INTEGER NOT NULL REFERENCES users(id),
    title        TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    layout       TEXT NOT NULL DEFAULT 'pages' CHECK (layout IN ('pages', 'single')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id         INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    question_number INTEGER NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'multiple_choice'
                    CHECK (kind IN ('multiple_choice', 'true_false', 'short_answer', 'long_answer')),
    prompt          TEXT NOT NULL DEFAULT '',
    options_json    TEXT NOT NULL DEFAULT '[]',
    answer          TEXT NOT NULL DEFAULT '',
    explanation     TEXT NOT NULL DEFAULT '',
    marks           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    subject_id     INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    grade          TEXT NOT NULL,
    filename       TEXT NOT NULL UNIQUE,
    original_name  TEXT NOT NULL,
    extracted_text TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, extracted_text, content='documents', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, extracted_text) VALUES (new.id, new.title, new.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, extracted_text) VALUES ('delete', old.id, old.title, old.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, extracted_text) VALUES ('delete', old.id, old.title, old.extracted_text);
    INSERT INTO documents_fts(rowid, title, extracted_text) VALUES (new.id, new.title, new.extracted_text);
END;

CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id);
CREATE INDEX IF NOT EXISTS idx_lessons_class   ON lessons(class_id);
CREATE INDEX IF NOT EXISTS idx_pages_lesson    ON lesson_pages(lesson_id, page_number);
CREATE INDEX IF NOT EXISTS idx_tests_class     ON tests(class_id);
CREATE INDEX IF NOT EXISTS idx_questions_test  ON test_questions(test_id, question_number);
CREATE INDEX IF NOT EXISTS idx_documents_scope ON documents(grade, subject_id);
"""

DEFAULT_SUBJECTS = [
    "Mathematics", "English", "Science", "Shona", "Ndebele",
    "Heritage Studies", "Agriculture", "ICT",
]

# Zimbabwean grade/form levels — fixed list, used by both the class-creation
# dropdown and the document-upload dropdown so the two can be matched exactly.
GRADES = [f"Grade {n}" for n in range(1, 8)] + [f"Form {n}" for n in range(1, 7)]


MIGRATIONS = [
    # CREATE TABLE IF NOT EXISTS doesn't alter existing tables, so column
    # additions go here; each is a no-op once applied.
    "ALTER TABLE tests ADD COLUMN layout TEXT NOT NULL DEFAULT 'pages'",
    "ALTER TABLE settings ADD COLUMN ai_provider TEXT NOT NULL DEFAULT 'cloud'",
    "ALTER TABLE settings ADD COLUMN ai_api_key TEXT",
    "ALTER TABLE settings ADD COLUMN ai_model_filename TEXT",
    "ALTER TABLE classes ADD COLUMN grade TEXT",
    "ALTER TABLE lesson_pages ADD COLUMN presentable INTEGER NOT NULL DEFAULT 1",
]


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        for stmt in MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # already applied
        # Seed subjects once (admin can add more later)
        count = conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO subjects (name) VALUES (?)",
                [(s,) for s in DEFAULT_SUBJECTS],
            )
        conn.commit()
    finally:
        conn.close()


def setup_complete() -> bool:
    conn = connect()
    try:
        row = conn.execute("SELECT setup_complete FROM settings WHERE id = 1").fetchone()
        return bool(row and row["setup_complete"])
    finally:
        conn.close()
