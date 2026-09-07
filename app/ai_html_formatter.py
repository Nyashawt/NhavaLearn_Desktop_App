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
from typing import Dict, List


def _wrap(inner_html: str) -> str:
    return f'<div class="ql-editor">{inner_html}</div>'


def _clean_markdown_line(line: str) -> str:
    """Models sometimes ignore the 'plain text only' prompt instruction and
    slip in markdown anyway (# headers, - bullets, whole-line **bold**).
    Strips the structural markers a line-oriented plain-text formatter can't
    otherwise handle; _inline_bold() below converts any remaining **bold**
    spans into real <strong> tags rather than leaving literal asterisks."""
    line = line.strip()
    # '#' has no legitimate use in this educational content, so strip it
    # wherever it appears, not just at line-start (models sometimes emit
    # '### ' mid-line after a prefix like 'Question 1: ### ...').
    line = re.sub(r'#{1,6}\s*', '', line)
    # bullet markers only at line-start — a leading '-' mid-sentence could be
    # a legitimate dash, not a markdown list item.
    line = re.sub(r'^[-*+]\s+', '', line)
    line = re.sub(r'^\*\*(.+)\*\*$', r'\1', line)
    return line.strip()


def _inline_bold(escaped_line: str) -> str:
    """Converts **text** to <strong>text</strong>. Runs on already-escaped
    text — ** has no HTML significance so html.escape() leaves it untouched,
    and the captured group was already escaped, so this is safe."""
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped_line)


def _clean_markdown_text(text: str) -> str:
    """Applies _clean_markdown_line() to every line before any block-splitting
    regex runs — a stray '### Question 1:' would otherwise never match a
    splitter looking for a line that starts with 'Question', so cleaning has
    to happen before structural parsing, not just before HTML escaping."""
    return "\n".join(_clean_markdown_line(l) for l in text.strip().split("\n"))


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
    cleaned = _clean_markdown_text(text)
    blocks = re.split(r'\n(?=Question\s+\d+:)', cleaned)
    parts = []
    for block in blocks:
        lines = [_inline_bold(html.escape(l)) for l in block.strip().split('\n') if l.strip()]
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
    cleaned = _clean_markdown_text(text)
    cards = re.split(r'\n(?=CARD\s+\d+)', cleaned, flags=re.IGNORECASE)
    parts = []
    for card in cards:
        front = re.search(r'Front:\s*(.+)', card, re.IGNORECASE)
        back = re.search(r'Back:\s*(.+)', card, re.IGNORECASE)
        if not front:
            continue
        parts.append(f"<p><strong>{_inline_bold(html.escape(front.group(1).strip()))}</strong></p>")
        if back:
            parts.append(f"<p>{_inline_bold(html.escape(back.group(1).strip()))}</p>")
    return _wrap("".join(parts))


def lesson_plan_text_to_quill_html(text: str) -> str:
    """
    Input shape: numbered sections ('1. Learning Objectives\\n...').
    Section headers become bold lines rather than <h1>/<h2> tags —
    keeps the output visually consistent with the rest of a lesson
    page, which is normally authored freehand in Quill, not structured
    like a document.
    """
    section_re = re.compile(r'^\d+\.\s+[A-Za-z][A-Za-z ]+$')
    parts = []
    for line in _clean_markdown_text(text).split('\n'):
        line = line.strip()
        if not line:
            continue
        escaped = _inline_bold(html.escape(line))
        if section_re.match(line):
            parts.append(f"<p><strong>{escaped}</strong></p>")
        else:
            parts.append(f"<p>{escaped}</p>")
    return _wrap("".join(parts))


def lesson_plan_text_to_pages(text: str) -> List[Dict[str, str]]:
    """Splits generate_lesson_draft()'s 7 numbered sections (Learning
    Objectives, Materials Needed, Introduction, Main Activity, Assessment,
    Conclusion, Homework — always in that order per the prompt) into the
    pages that actually get presented to the class. These lesson_pages rows
    are the student-facing view (there's no separate teacher-only copy), so
    planning sections meant for the teacher (Objectives, Materials,
    Introduction, Assessment, Conclusion) are deliberately dropped here —
    only Main Activity and Homework carry forward as pages. Falls back to a
    single page if the model didn't follow the numbered format."""
    section_re = re.compile(r'^\d+\.\s+(.+)$')
    sections: List[tuple] = []
    current = None
    for line in _clean_markdown_text(text).split('\n'):
        line = line.strip()
        if not line:
            continue
        m = section_re.match(line)
        if m:
            current = (m.group(1).strip(), [])
            sections.append(current)
        elif current:
            current[1].append(line)

    if not sections:
        return [{"title": "Lesson Plan", "content_html": unwrap(lesson_plan_text_to_quill_html(text))}]

    def _find(keyword):
        for sec_title, body_lines in sections:
            if keyword in sec_title.lower():
                return (sec_title, body_lines)
        return None

    groups = [
        ("Main Activity", [_find("main activity")]),
        ("Homework", [_find("homework")]),
    ]

    pages = []
    for title, group_sections in groups:
        group_sections = [s for s in group_sections if s]
        if not group_sections:
            continue
        parts = []
        for sec_title, body_lines in group_sections:
            parts.append(f"<p><strong>{_inline_bold(html.escape(sec_title))}</strong></p>")
            for bl in body_lines:
                parts.append(f"<p>{_inline_bold(html.escape(bl))}</p>")
        pages.append({"title": title, "content_html": "".join(parts)})

    if not pages:
        return [{"title": "Lesson Plan", "content_html": unwrap(lesson_plan_text_to_quill_html(text))}]
    return pages
