"""
NhavaLearn AI Generator.

Ported from IntelliSchool's app/local_llm_service.py + app/main.py's
smart_generate(), collapsed into one module — no FastAPI, no SQLAlchemy,
called directly and synchronously from api.py (see api.py's ai_generate_*
methods). pywebview already runs each js_api call on its own thread, so
this file doesn't need to manage threading itself — same reason
phet_download() in api.py can block on a network call with no special
handling.

Cloud branch: same idea as IntelliSchool's generate_with_anthropic().
Local branch: ported from local_llm_service.py, simplified for the fixed
target hardware (i5-12450H, no GPU, 16 GB RAM): CUDA detection is dropped
entirely, n_gpu_layers is always 0. Config comes from the settings table
(see ai_settings.py) rather than a .env file, since NhavaLearn has none.
"""
import os
import re
import threading
import logging
import multiprocessing
from pathlib import Path
from typing import Dict, List

from . import db, ai_settings, documents
from .ai_pagination import (
    generate_with_pagination,
    format_paginated_content,
    calculate_optimal_tokens,
)

logger = logging.getLogger(__name__)


class AIUnavailable(Exception):
    """Raised when neither cloud nor local generation is usable."""


# ============================================================
# CLOUD BRANCH
# ============================================================

def _generate_with_cloud(prompt: str, system_prompt: str, max_tokens: int) -> str:
    api_key = ai_settings.get_ai_settings()["api_key"]
    if not api_key:
        raise AIUnavailable("no cloud API key configured")

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ============================================================
# LOCAL BRANCH — ported from local_llm_service.py
# ============================================================

_model_lock = threading.Lock()  # guards first-load only; generation itself
_model = None                   # is fine to run concurrently once loaded
_model_info: Dict = {}


def get_model_dir() -> Path:
    """Models live under the same %LOCALAPPDATA%\\NhavaLearn\\models tree
    as everything else — db.data_dir() is the existing resolver."""
    model_dir = Path(db.data_dir()) / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def detect_cpu_threads() -> int:
    """Physical-core estimate for llama.cpp — unchanged from IntelliSchool:
    logical // 2, floor of 2, never exceeding logical count."""
    try:
        logical = multiprocessing.cpu_count() or 4
        return min(max(2, logical // 2), logical)
    except Exception:
        return 4


def get_model_path() -> Path:
    filename = ai_settings.get_ai_settings()["model_filename"]
    model_dir = get_model_dir()

    if not filename:
        # "Largest .gguf present" — same heuristic as IntelliSchool's
        # select_best_model(), useful before an admin has picked one.
        candidates = sorted(model_dir.glob("*.gguf"), key=lambda p: p.stat().st_size, reverse=True)
        if not candidates:
            raise FileNotFoundError(
                f"No local model found in {model_dir}. Download a GGUF model "
                f"via Admin \u2192 AI Settings, or configure cloud generation."
            )
        return candidates[0]

    model_path = (model_dir / filename).resolve()
    if not model_path.exists():
        raise FileNotFoundError(f"Configured model not found: {model_path}")
    return model_path


def detect_model_format(model_path: Path) -> str:
    name = model_path.name.lower()
    if "qwen" in name:
        return "qwen"
    if "phi" in name:
        return "phi"
    if "gemma" in name:
        return "gemma"
    return "generic"


def _format_prompt(model_format: str, prompt: str, system: str) -> str:
    if model_format == "qwen":
        return (f"<|im_start|>system\n{system}<|im_end|>\n"
                f"<|im_start|>user\n{prompt}<|im_end|>\n"
                f"<|im_start|>assistant\n")
    if model_format == "phi":
        return (f"<|system|>\n{system}<|end|>\n"
                f"<|user|>\n{prompt}<|end|>\n"
                f"<|assistant|>\n")
    if model_format == "gemma":
        return f"<start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
    return f"{system}\n\n{prompt}"


def load_model():
    """Lazy singleton, loaded on first use rather than app startup — a
    laptop that never opens AI Settings shouldn't pay the 30-90s load
    cost every launch. n_gpu_layers is hard-coded to 0: the target
    hardware has no GPU, so nvidia-smi detection is dropped, not ported."""
    global _model, _model_info
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        from llama_cpp import Llama  # imported lazily: not everyone has it installed

        model_path = get_model_path()
        model_format = detect_model_format(model_path)
        n_threads = detect_cpu_threads()

        logger.info(f"Loading local model: {model_path.name} ({n_threads} threads, CPU-only)")

        _model = Llama(
            model_path=str(model_path),
            n_ctx=4096,
            n_threads=n_threads,
            n_gpu_layers=0,
            n_batch=256,  # CPU-tuned, not the GPU default of 512
            use_mmap=True,
            use_mlock=os.name != "nt",
            verbose=False,
        )
        _model_info = {
            "model": model_path.name, "format": model_format,
            "threads": n_threads, "backend": "llama.cpp", "offline": True,
        }
        logger.info("Local model loaded")
        return _model


def _generate_with_local(prompt: str, system_prompt: str, max_tokens: int) -> str:
    model = load_model()
    formatted = _format_prompt(_model_info.get("format", "generic"), prompt, system_prompt)
    stop_tokens = ["<|im_end|>", "<|end|>", "<end_of_turn>", "</s>", "[INST]"]

    response = model(
        formatted, max_tokens=max_tokens, temperature=0.7, top_p=0.9,
        repeat_penalty=1.15, top_k=40, stop=stop_tokens, echo=False,
    )
    return response["choices"][0]["text"].strip()


def get_ai_status() -> Dict:
    settings = ai_settings.get_ai_settings()
    local_ready = False
    try:
        get_model_path()
        local_ready = True
    except FileNotFoundError:
        pass
    return {
        "provider": settings["provider"],
        "cloud_configured": bool(settings["api_key"]),
        "local_model_loaded": _model is not None,
        "local_model_available": local_ready,
        "local_model_info": _model_info if _model is not None else None,
    }


# ============================================================
# SMART SWITCH — same shape as IntelliSchool's smart_generate()
# ============================================================

def smart_generate(prompt: str, system_prompt: str, max_tokens: int) -> str:
    """Cloud-first, local-fallback — matches IntelliSchool's main.py."""
    settings = ai_settings.get_ai_settings()
    provider = settings["provider"]

    if provider in ("cloud", "auto") and settings["api_key"]:
        try:
            logger.info("Using cloud generation (Anthropic)")
            return _generate_with_cloud(prompt, system_prompt, max_tokens)
        except Exception as e:
            if provider == "cloud":
                raise AIUnavailable(f"Cloud generation failed: {e}") from e
            logger.warning(f"Cloud generation failed ({e}), falling back to local")

    try:
        logger.info("Using local generation")
        return _generate_with_local(prompt, system_prompt, max_tokens)
    except (FileNotFoundError, ImportError) as e:
        raise AIUnavailable(
            "AI is not available on this device \u2014 no local model is configured "
            "and cloud generation is off. Ask your administrator to set it up in "
            "Admin \u2192 AI Settings."
        ) from e


# ============================================================
# CONTENT-TYPE GENERATION FUNCTIONS
# ============================================================

def _reference_context(grade: str, subject_id, topic: str) -> str:
    """Pulls matching excerpts from admin-uploaded library documents (FTS5
    keyword search — see documents.py) and folds them into the prompt. Applies
    uniformly to cloud and local generation since this runs before
    smart_generate() is ever called. Empty string (not an error) when no
    documents match — generation still proceeds on model knowledge alone."""
    if not subject_id:
        return ""
    try:
        excerpts = documents.search_documents(grade, subject_id, topic, limit=3)
    except Exception:
        return ""
    if not excerpts:
        return ""
    return (
        "\n\nReference material from the school's library (ground your answer "
        "in this where relevant):\n" + "\n---\n".join(excerpts)
    )


def generate_quiz(topic: str, num_questions: int, grade: str, subject_id=None, quiz_type: str = "multiple_choice") -> Dict:
    system = "You are creating quiz questions for a Zimbabwean classroom."
    user = (
        f"Create {num_questions} {quiz_type} questions on: {topic}\nGrade: {grade}\n\n"
        "Format each question EXACTLY like this, with no other text:\n"
        "Question 1: <question text>\n"
        "A) <option>\nB) <option>\nC) <option>\nD) <option>\n"
        "Answer: <letter>\n"
        "Explanation: <short explanation>\n"
        "Question 2: ...\n"
        "(continue sequentially for all questions)"
        + _reference_context(grade, subject_id, topic)
    )
    optimal_tokens = calculate_optimal_tokens(num_questions, "quiz")
    content, meta = generate_with_pagination(
        generate_function=lambda p, s, t: smart_generate(p, s, t),
        user_prompt=user, system_prompt=system, max_tokens=optimal_tokens,
        requested_count=num_questions, content_type="questions",
    )
    return {"content": format_paginated_content(content, meta), "meta": meta}


def generate_flashcards(topic: str, num_cards: int, grade: str, subject_id=None) -> Dict:
    system = "You are creating study flashcards for a Zimbabwean classroom."
    user = (
        f"Create {num_cards} flashcards on: {topic}\nGrade: {grade}\n\n"
        "Format each flashcard EXACTLY like this, with no other text:\n"
        "CARD 1\nFront: <question or term>\nBack: <answer or definition>\n"
        "CARD 2\n...\n"
        "(continue sequentially for all cards)"
        + _reference_context(grade, subject_id, topic)
    )
    optimal_tokens = calculate_optimal_tokens(num_cards, "flashcards")
    content, meta = generate_with_pagination(
        generate_function=lambda p, s, t: smart_generate(p, s, t),
        user_prompt=user, system_prompt=system, max_tokens=optimal_tokens,
        requested_count=num_cards, content_type="flashcards",
    )
    return {"content": format_paginated_content(content, meta), "meta": meta}


def generate_lesson_draft(topic: str, grade: str, subject_id=None, duration: str = "60 minutes") -> Dict:
    """Not paginated — a lesson plan is one document, not N discrete items."""
    system = "You are an experienced educator creating lesson plans for a Zimbabwean classroom."
    user = (
        f"Create a lesson plan for: {topic}\nGrade: {grade}, Duration: {duration}\n\n"
        "Include: 1. Learning Objectives 2. Materials Needed 3. Introduction "
        "4. Main Activity 5. Assessment 6. Conclusion 7. Homework"
        + _reference_context(grade, subject_id, topic)
    )
    content = smart_generate(user, system, max_tokens=2000)
    return {"content": content, "meta": {"paginated": False}}


# ============================================================
# STRUCTURED PARSING (for creating real test_questions rows,
# as opposed to ai_html_formatter's Quill-HTML output)
# ============================================================

def parse_quiz_to_questions(text: str) -> List[Dict]:
    """Parses generate_quiz()'s raw 'Question N: / A)-D) / Answer: /
    Explanation:' text into structured rows matching test_questions'
    columns (kind/prompt/options_json/answer/explanation/marks). Used by
    the Classes 'Generate with AI' -> Test flow; the old Quill HTML path
    (ai_html_formatter.quiz_text_to_quill_html) is unrelated and unaffected."""
    blocks = re.split(r'\n(?=Question\s+\d+:)', text.strip())
    questions = []
    option_re = re.compile(r'^([A-D])\)\s*(.+)$')
    for block in blocks:
        lines = [l.strip() for l in block.strip().split('\n') if l.strip()]
        if not lines:
            continue
        m = re.match(r'Question\s+\d+:\s*(.+)', lines[0])
        if not m:
            continue
        prompt = m.group(1).strip()
        options, answer, explanation = [], "", ""
        for line in lines[1:]:
            opt = option_re.match(line)
            if opt:
                options.append(opt.group(2).strip())
                continue
            if line.lower().startswith("answer:"):
                answer = line.split(":", 1)[1].strip()
            elif line.lower().startswith("explanation:"):
                explanation = line.split(":", 1)[1].strip()
        if not prompt:
            continue
        questions.append({
            "kind": "multiple_choice",
            "prompt": prompt,
            "options": options,
            "answer": answer,
            "explanation": explanation,
            "marks": 1,
        })
    return questions
