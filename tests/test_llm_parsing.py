"""Unit tests for the pure text-parsing helpers in services/llm.py —
_extract_text and _strip_markdown_fences. Both were added as fixes for
real bugs found during integration testing with the live Anthropic API
(see README, "Found and fixed during testing"); these tests lock those
fixes in place so they can't silently regress.

No API calls here — _extract_text takes a plain response-shaped object,
not a real Anthropic response, so this stays fast and free."""

from types import SimpleNamespace

import pytest

from services.llm import _extract_text, _strip_markdown_fences


def _block(type_: str, text: str | None = None):
    """A minimal stand-in for Anthropic's ContentBlock — only the
    attributes _extract_text actually reads."""
    return SimpleNamespace(type=type_, text=text)


class TestExtractText:
    def test_single_text_block_returns_its_text(self):
        response = SimpleNamespace(content=[_block("text", "hello")])
        assert _extract_text(response) == "hello"

    def test_thinking_block_before_text_block_is_skipped(self):
        """The exact real-world case: Claude Sonnet 5's extended thinking
        prepends a ThinkingBlock with no .text attribute before the
        actual answer."""
        response = SimpleNamespace(content=[_block("thinking"), _block("text", "the real answer")])
        assert _extract_text(response) == "the real answer"

    def test_no_text_block_at_all_raises(self):
        response = SimpleNamespace(content=[_block("thinking")])
        with pytest.raises(ValueError):
            _extract_text(response)

    def test_empty_content_list_raises(self):
        response = SimpleNamespace(content=[])
        with pytest.raises(ValueError):
            _extract_text(response)


class TestStripMarkdownFences:
    def test_plain_json_is_returned_unchanged(self):
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fence_is_stripped(self):
        """The exact real-world case: Claude wraps output in ```json
        fences despite the prompt saying not to."""
        wrapped = '```json\n{"a": 1}\n```'
        assert _strip_markdown_fences(wrapped) == '{"a": 1}'

    def test_bare_fence_without_language_tag_is_stripped(self):
        wrapped = '```\n{"a": 1}\n```'
        assert _strip_markdown_fences(wrapped) == '{"a": 1}'

    def test_leading_trailing_whitespace_is_trimmed(self):
        assert _strip_markdown_fences('  \n{"a": 1}\n  ') == '{"a": 1}'