"""
Headless tests for ai_generator.py / ai_html_formatter.py.
Run alongside tests/test_backend.py after any change to app/ai_*.py.

Mocks smart_generate's dependencies (cloud + local) rather than the
network or a real model — mirrors the existing suite's approach of
stubbing webview instead of hitting real GUI. No API key or GGUF file
needed to run these.
"""
import unittest
from unittest.mock import patch

from app.ai_html_formatter import (
    quiz_text_to_quill_html,
    flashcards_text_to_quill_html,
    lesson_plan_text_to_quill_html,
    lesson_plan_text_to_pages,
)
from app.ai_generator import parse_quiz_to_questions


class TestHtmlFormatters(unittest.TestCase):
    def test_quiz_formatting_wraps_in_ql_editor(self):
        raw = "Question 1: What is 2+2?\nA) 3\nB) 4\nAnswer: B"
        out = quiz_text_to_quill_html(raw)
        self.assertTrue(out.startswith('<div class="ql-editor">'))
        self.assertIn("<strong>Question 1: What is 2+2?</strong>", out)
        self.assertIn("<p>Answer: B</p>", out)

    def test_quiz_formatting_escapes_html(self):
        raw = "Question 1: What is <script>alert(1)</script>?\nAnswer: none"
        out = quiz_text_to_quill_html(raw)
        self.assertNotIn("<script>", out)

    def test_flashcards_formatting(self):
        raw = "CARD 1\nFront: Photosynthesis\nBack: How plants make food"
        out = flashcards_text_to_quill_html(raw)
        self.assertIn("<strong>Photosynthesis</strong>", out)
        self.assertIn("<p>How plants make food</p>", out)

    def test_lesson_plan_bolds_numbered_sections_only(self):
        raw = "1. Learning Objectives\nStudents will understand X.\n2. Materials Needed\nChalk"
        out = lesson_plan_text_to_quill_html(raw)
        self.assertIn("<strong>1. Learning Objectives</strong>", out)
        self.assertIn("<p>Students will understand X.</p>", out)

    def test_strips_markdown_headers_and_bullets(self):
        raw = "Question 1: ### What is 2+2?\n- A) 3\n- B) 4\nAnswer: B"
        out = quiz_text_to_quill_html(raw)
        self.assertNotIn("#", out)
        self.assertNotIn("- A)", out)
        self.assertIn("A) 3", out)

    def test_converts_markdown_bold_to_strong_tag(self):
        raw = "Question 1: What is **half** of 4?\nAnswer: 2"
        out = quiz_text_to_quill_html(raw)
        self.assertIn("<strong>half</strong>", out)
        self.assertNotIn("**", out)

    def test_lesson_plan_splits_into_three_pages(self):
        raw = (
            "1. Learning Objectives\nAdd fractions.\n"
            "2. Materials Needed\nChalk.\n"
            "3. Introduction\nRecap prior lesson.\n"
            "4. Main Activity\nWork through examples.\n"
            "5. Assessment\nQuick quiz.\n"
            "6. Conclusion\nSummarise.\n"
            "7. Homework\nExercise 3."
        )
        pages = lesson_plan_text_to_pages(raw)
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[0]["title"], "Introduction")
        self.assertIn("Learning Objectives", pages[0]["content_html"])
        self.assertIn("Introduction", pages[0]["content_html"])
        self.assertEqual(pages[1]["title"], "Main Activity")
        self.assertIn("Work through examples", pages[1]["content_html"])
        self.assertEqual(pages[2]["title"], "Assessment & Wrap-up")
        self.assertIn("Homework", pages[2]["content_html"])

    def test_lesson_plan_pages_falls_back_when_unstructured(self):
        pages = lesson_plan_text_to_pages("Just some free-form text with no numbered sections.")
        self.assertEqual(len(pages), 1)
        self.assertIn("free-form text", pages[0]["content_html"])


class TestQuizParsing(unittest.TestCase):
    def test_strips_markdown_from_structured_fields(self):
        raw = "Question 1: ### What is **half** of 4?\nA) 1\nB) 2\nAnswer: **B**\nExplanation: ### Simple division."
        questions = parse_quiz_to_questions(raw)
        self.assertEqual(len(questions), 1)
        q = questions[0]
        self.assertNotIn("#", q["prompt"])
        self.assertNotIn("**", q["prompt"])
        self.assertEqual(q["answer"], "B")
        self.assertNotIn("#", q["explanation"])


class TestSmartGenerateFallback(unittest.TestCase):
    """
    Confirms the cloud-first / local-fallback switch actually switches,
    without needing a real API key or GGUF file present.
    """

    @patch("app.ai_generator.ai_settings.get_ai_settings")
    @patch("app.ai_generator._generate_with_cloud")
    def test_uses_cloud_when_configured(self, mock_cloud, mock_settings):
        mock_settings.return_value = {"provider": "cloud", "api_key": "sk-test", "model_filename": None, "cpu_threads": None}
        mock_cloud.return_value = "cloud output"

        from app.ai_generator import smart_generate
        result = smart_generate("prompt", "system", 100)

        self.assertEqual(result, "cloud output")
        mock_cloud.assert_called_once()

    @patch("app.ai_generator.get_model_path")
    @patch("app.ai_generator.ai_settings.get_ai_settings")
    def test_raises_ai_unavailable_when_nothing_configured(self, mock_settings, mock_model_path):
        mock_settings.return_value = {"provider": "cloud", "api_key": None, "model_filename": None, "cpu_threads": None}
        mock_model_path.side_effect = FileNotFoundError("no model found")

        from app.ai_generator import smart_generate, AIUnavailable
        with self.assertRaises(AIUnavailable):
            smart_generate("prompt", "system", 100)


if __name__ == "__main__":
    unittest.main()
