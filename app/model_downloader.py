"""
First-run download of the default local AI model.

The GGUF file (~1.1 GB) is too big to ship inside the installer we email to
schools, so the installer ships without it and the app fetches it on first
launch instead — in a background thread, straight into the same
%LOCALAPPDATA%\\NhavaLearn\\models folder ai_generator.get_model_dir() reads.

Built for flaky school connections (4G dongles):
  * downloads to "<name>.part" and resumes with an HTTP Range request, so a
    dropped connection or a closed laptop picks up where it left off on the
    next attempt / next launch instead of starting over;
  * retries with backoff a few times before giving up until the next launch
    (or until an admin presses "Retry" in AI Settings);
  * only renames .part -> .gguf after size + SHA-256 match, so a truncated or
    corrupt file is never picked up by get_model_path() (which globs *.gguf).
"""
import hashlib
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Dict

from . import ai_generator

logger = logging.getLogger(__name__)

# Qwen's official GGUF release on Hugging Face — the model the app was tuned on.
MODEL_FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_URL = ("https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/"
             + MODEL_FILENAME)
MODEL_SIZE = 1117320736
MODEL_SHA256 = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"

CHUNK = 1024 * 1024          # hashing block size
NET_CHUNK = 64 * 1024        # small reads so progress moves on slow 4G links
MAX_ATTEMPTS = 5

_lock = threading.Lock()
_thread = None
_status: Dict = {"state": "idle", "downloaded": 0, "total": MODEL_SIZE, "error": None}


def get_status() -> Dict:
    return dict(_status)


def _set(**kw) -> None:
    _status.update(kw)


def _has_any_model() -> bool:
    return any(ai_generator.get_model_dir().glob("*.gguf"))


def start_if_needed() -> bool:
    """Kick off the background download unless a model is already present
    or a download is already running. Safe to call on every launch."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        if _has_any_model():
            _set(state="done", downloaded=MODEL_SIZE, error=None)
            return False
        _set(state="downloading", error=None)
        _thread = threading.Thread(target=_run, name="model-download", daemon=True)
        _thread.start()
        return True


def _run() -> None:
    dest = ai_generator.get_model_dir() / MODEL_FILENAME
    part = dest.with_name(dest.name + ".part")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _download(part)
            _set(state="verifying")
            if not _verify(part):
                # Corrupt — throw it away and start clean on the next attempt.
                part.unlink(missing_ok=True)
                raise IOError("downloaded file failed its integrity check")
            os.replace(part, dest)
            _set(state="done", downloaded=MODEL_SIZE, error=None)
            logger.info(f"Local AI model downloaded to {dest}")
            return
        except Exception as e:
            logger.warning(f"Model download attempt {attempt} failed: {e}")
            _set(state="downloading", error=str(e))
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(60, 5 * attempt))

    _set(state="error", error=_status.get("error") or "download failed")


def _download(part) -> None:
    have = part.stat().st_size if part.exists() else 0
    if have >= MODEL_SIZE:
        _set(downloaded=have)
        return

    headers = {"User-Agent": "NhavaLearn-Desktop"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(MODEL_URL, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # range past the end: .part is already complete
            return
        raise

    with resp:
        if have and resp.status != 206:
            have = 0  # server ignored the Range header — start over
        _set(downloaded=have)
        with open(part, "ab" if have else "wb") as f:
            while True:
                chunk = resp.read(NET_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                have += len(chunk)
                _set(downloaded=have)

    if have < MODEL_SIZE:
        raise IOError(f"connection closed early ({have} of {MODEL_SIZE} bytes)")


def _verify(part) -> bool:
    if part.stat().st_size != MODEL_SIZE:
        return False
    h = hashlib.sha256()
    with open(part, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest() == MODEL_SHA256
