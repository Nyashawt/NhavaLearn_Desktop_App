"""
Converts ai_generator.py's free-text output into Quill-compatible HTML.

NhavaLearn's rendering-parity rule (see tech doc, section 7): any surface
that displays lesson content must wrap it in <div class="ql-editor"> —
specifically a div, since blot-formatter2 scopes alignment rules as
`div.ql-editor ...`. IntelliSchool never needed this layer because its
output went straight to a web page; NhavaLearn's output has to survive
being loaded into the Quill editor, previewed, and presented.

Each content shape (quiz, flashcards, lesson plan) gets its own small
formatter rather than one generic one — the input text shapes are
different enough (numbered questions vs. CARD N Front/Back vs. numbered
sections) that a single regex would be fragile. Keep these covered by
tests/test_backend.py; a formatting miss here is a silent content bug,
not a crash.
"""
import re
import html


def _wrap(inner_html: str) -> str:
    return f'<div class="ql-editor">{inner_html}</div>'


_WRAPPER_RE = re.compile(r'^<div class="ql-editor">(.*)</div>$', re.DOTALL)


def unwrap(wrapped_html: str) -> str:
    """Strips the <div class="ql-editor"> wrapper _wrap() adds. Needed when
    storing generated content as a lesson_pages.content_html value: that
    column holds bare inner HTML (Present/Preview add the ql-editor wrapper
    themselves at render time — see app.js's lesson-content rendering), and
    Quill's editor loads content_html via direct DOM injection into an
    element that already has the ql-editor class — a second nested wrapper
    is an unrecognized blot and gets silently dropped, leaving a blank
    editor even though the content is saved correctly in the database."""
    m = _WRAPPER_RE.match(wrapped_html)
    return m.group(1) if m else wrapped_html


def quiz_text_to_quill_html(text: str) -> str:
    """
    Input shape: 'Question 1: ...\\nA) ...\\nAnswer: ...\\nExplanation: ...'
    Bolds the question line, leaves options/answer/explanation as plain
    paragraphs. One <p> per line — matches how Quill itself stores
    line breaks, so re-editing in the app looks native, not pasted.
    """
    blocks = re.split(r'\n(?=Question\s+\d+:)', text.strip())
    parts = []
    for block in blocks:
        lines = [html.escape(l) for l in block.strip().split('\n') if l.strip()]
        if not lines:
            continue
        parts.append(f"<p><strong>{lines[0]}</strong></p>")
        for line in lines[1:]:
            parts.append(f"<p>{line}</p>")
    return _wrap("".join(parts))


def flashcards_text_to_quill_html(text: str) -> str:
    """
    Input shape: 'CARD 1\\nFront: ...\\nBack: ...'
    Rendered as a front/back pair per card, front bolded so it reads as
    a prompt when scanned quickly in the editor.
    """
    cards = re.split(r'\n(?=CARD\s+\d+)', text.strip(), flags=re.IGNORECASE)
    parts = []
    for card in cards:
        front = re.search(r'Front:\s*(.+)', card, re.IGNORECASE)
        back = re.search(r'Back:\s*(.+)', card, re.IGNORECASE)
        if not front:
            continue
        parts.append(f"<p><strong>{html.escape(front.group(1).strip())}</strong></p>")
        if back:
            parts.append(f"<p>{html.escape(back.group(1).strip())}</p>")
    return _wrap("".join(parts))


def lesson_plan_text_to_quill_html(text: str) -> str:
    """
    Input shape: numbered sections ('1. Learning Objectives\\n...').
    Section headers become bold lines rather than <h1>/<h2> tags —
    keeps the output visually consistent with the rest of a lesson
    page, which is normally authored freehand in Quill, not structured
    like a document.
    """
    section_re = re.compile(r'^\d+\.\s+[A-Z][A-Za-z ]+$')
    parts = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        escaped = html.escape(line)
        if section_re.match(line):
            parts.append(f"<p><strong>{escaped}</strong></p>")
        else:
            parts.append(f"<p>{escaped}</p>")
    return _wrap("".join(parts))
