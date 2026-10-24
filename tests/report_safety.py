"""Checks that a rendered report cannot be read as active content.

Markdown is checked twice: structurally, and by parsing it with markdown-it-py in its GFM-like
mode with linkify-it-py, so bare-URL and email autolinks show up as link tokens.

The parser check is stricter than GitHub on purpose. Linkify runs after escaped text is joined
back together (markdown-it's default runs it before, where a backslash escape alone would hide
a link), and linkify-it's fuzzy matching also links bare domains (example.com) and
protocol-relative addresses (//example.com), which GFM does not.
"""

import json
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.rules_core import linkify
from markdown_it.token import Token

from toolguard.rules.text import is_hidden

MAX_LINE_CHARS = 4_000
H2_HEADINGS = ["Verdict", "AI review", "Summary", "Findings", "Tools scanned", "Method"]
_ACTIVE = {"link_open", "image", "html_inline", "html_block", "autolink"}

_parser = MarkdownIt("gfm-like")
_parser.core.ruler.disable("linkify")
_parser.core.ruler.push("linkify_after_join", linkify)


def _walk(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    for token in tokens:
        out.append(token)
        out.extend(_walk(token.children or []))
    return out


def assert_inert_markdown(text: str) -> list[Token]:
    """Asserts and returns all parsed tokens, for further checks by the caller."""
    assert text.isascii(), "Markdown report must be ASCII"
    lines = text.split("\n")
    assert all(line.isprintable() for line in lines), "control characters in the report"
    assert max(len(line) for line in lines) <= MAX_LINE_CHARS

    tokens = _walk(_parser.parse(text))
    assert not [t.type for t in tokens if t.type in _ACTIVE]

    # A broken fence would swallow the headings after it.
    headings = [
        tokens[i + 1].content
        for i, t in enumerate(tokens)
        if t.type == "heading_open" and t.tag == "h2"
    ]
    assert headings == H2_HEADINGS

    # Tables hold only our own words; every row has the header's cell count.
    rows: list[int] = []
    cells = 0
    for token in tokens:
        if token.type == "tr_open":
            cells = 0
        elif token.type in ("th_open", "td_open"):
            cells += 1
        elif token.type == "tr_close":
            rows.append(cells)
    assert all(count == 2 for count in rows)

    return tokens


def code_contents(tokens: list[Token]) -> str:
    """All text inside code spans and fences, joined."""
    return "\n".join(t.content for t in tokens if t.type in ("code_inline", "fence"))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def assert_safe_json(text: str) -> Any:
    assert text.isascii()
    assert not any(c in text for c in "<>&"), "HTML-significant characters must be escaped"
    document = json.loads(text)
    for value in _strings(document):
        assert not any(is_hidden(c) for c in value), "hidden character in a JSON string"
    return document
