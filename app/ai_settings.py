"""AI settings — thin wrapper over the settings table.

Column additions live in db.py's MIGRATIONS list (same zero-step pattern
already used for tests.layout), not here — this file only reads/writes.
"""
from . import db


def get_ai_settings() -> dict:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT ai_provider, ai_api_key, ai_model_filename FROM settings WHERE id = 1"
        ).fetchone()
        if not row:
            return {"provider": "cloud", "api_key": None, "model_filename": None}
        return {
            "provider": row["ai_provider"] or "cloud",
            "api_key": row["ai_api_key"],
            "model_filename": row["ai_model_filename"],
        }
    finally:
        conn.close()


def set_ai_settings(provider=None, api_key=None, model_filename=None) -> None:
    conn = db.connect()
    try:
        fields, values = [], []
        if provider is not None:
            fields.append("ai_provider = ?"); values.append(provider)
        if api_key is not None:
            fields.append("ai_api_key = ?"); values.append(api_key)
        if model_filename is not None:
            fields.append("ai_model_filename = ?"); values.append(model_filename)
        if fields:
            conn.execute(f"UPDATE settings SET {', '.join(fields)} WHERE id = 1", values)
            conn.commit()
    finally:
        conn.close()
