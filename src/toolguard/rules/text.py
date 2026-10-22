"""Text helpers for rules: which text to scan, sentence windows, evidence, and safe display.

Offsets are always code point positions in the stored, unmodified text. Text shown in evidence
goes through visible(), so invisible and control characters appear by name, never raw.
"""

import json
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from toolguard.core.models import Evidence, Tool

MAX_SCAN_CHARS = 100_000  # per tool, so a huge schema cannot slow a scan
MAX_EVIDENCE_PER_FINDING = 5
CONTEXT_CHARS = 40
MAX_EXCERPT_CHARS = 200
WINDOW_CHARS = 400
WINDOW_OVERLAP = 100

# Characters that render as nothing (or as plain space) but are not ordinary whitespace.
_FILLERS = {"\u115f", "\u1160", "\u3164", "\uffa0", "\u2800"}
_VISIBLE_CONTROLS = {"\n", "\t"}
_BREAK = re.compile(r"[.!?](?=\s|$)|\n")


# Cyrillic and Greek letters that look like Latin ones. Small on purpose; used for matching
# only (never for hashing), so "ign\u043ere" still matches "ignore".
LOOKALIKE_TO_LATIN = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u0501\u04bb\u04cf\u051b\u051d"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0406\u0408\u0405"
    "\u03b1\u03bf\u03bd\u03c1\u03b9\u03ba\u03c5\u03c7"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7",
    "aeopcyxijsdhlqwABEKMHOPCTXIJSaovpikuxABEZHIKMNOPTYX",
)


@dataclass(frozen=True)
class View:
    """Text prepared for matching, with origin[i] the original offset of view character i.

    origin has one extra entry, the original length, so an end offset can always be mapped.
    """

    text: str
    origin: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Original offsets for the view span [start, end)."""
        if start == end:
            return self.origin[start], self.origin[start]
        return self.origin[start], self.origin[end - 1] + 1


def fold(text: str) -> View:
    """Copy of text for matching: invisible characters removed, unusual spaces made plain,
    look-alike letters mapped to Latin, and compatibility forms (fullwidth letters, ligatures)
    folded with NFKC, one character at a time."""
    out: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Zs":
            folded = " "
        elif is_hidden(char):
            continue
        else:
            mapped = char.translate(LOOKALIKE_TO_LATIN)
            folded = mapped if mapped != char else unicodedata.normalize("NFKC", char)
        out.append(folded)
        origin.extend([index] * len(folded))
    origin.append(len(text))
    return View("".join(out), tuple(origin))


def split_words(view: View) -> View:
    """View with schema word separators as spaces: _ and - between letters or digits, a dot
    between letters, and a camelCase boundary. read_all_api_keys reads as read all api keys."""
    text = view.text
    out: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        before = text[index - 1] if index else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        joined = before.isalnum() and after.isalnum()
        if (char in "_-" and joined) or (char == "." and before.isalpha() and after.isalpha()):
            out.append(" ")
            origin.append(view.origin[index])
            continue
        if char.isupper() and before.islower():
            out.append(" ")
            origin.append(view.origin[index])
        out.append(char)
        origin.append(view.origin[index])
    origin.append(view.origin[-1])
    return View("".join(out), tuple(origin))


@dataclass(frozen=True)
class TextField:
    """One piece of text to scan. path names where it came from, for evidence."""

    path: str
    text: str

    @cached_property
    def views(self) -> tuple[View, ...]:
        """Folded text for matching, plus a word-split copy for schema text."""
        folded = fold(self.text)
        if self.path.startswith("input_schema"):
            return folded, split_words(folded)
        return (folded,)


def text_fields(tool: Tool) -> tuple[list[TextField], str | None]:
    """The tool's text in scan order, and the path of the first field the cap cut short.

    Covers name, title, description, string annotations, and every string value and property
    name inside input_schema. Non-string values are skipped.
    """
    fields: list[TextField] = []
    budget = MAX_SCAN_CHARS
    for path, text in _all_text(tool):
        if not text:
            continue
        if budget <= 0:
            return fields, path
        if len(text) > budget:
            fields.append(TextField(path, text[:budget]))
            return fields, path
        fields.append(TextField(path, text))
        budget -= len(text)
    return fields, None


def _all_text(tool: Tool) -> Iterator[tuple[str, str]]:
    yield "name", tool.name
    if tool.title:
        yield "title", tool.title
    if tool.description:
        yield "description", tool.description
    for key, value in tool.annotations.items():
        if isinstance(value, str):
            yield f"annotations/{_escape(key)}", value
    yield from _schema_text(tool.input_schema)


def _schema_text(schema: Any) -> Iterator[tuple[str, str]]:
    """Every string in the schema, plus property names. Iterative, so depth cannot overflow."""
    stack: list[tuple[str, Any]] = [("input_schema", schema)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, dict):
            children = []
            for key, child in value.items():
                child_path = f"{path}/{_escape(str(key))}"
                if key == "properties" and isinstance(child, dict):
                    for name in child:
                        yield f"{child_path}/{_escape(str(name))} (property name)", str(name)
                children.append((child_path, child))
            stack.extend(reversed(children))
        elif isinstance(value, list):
            stack.extend(reversed([(f"{path}/{i}", item) for i, item in enumerate(value)]))


def _escape(key: str) -> str:
    """JSON Pointer escaping (RFC 6901) for one path segment."""
    return key.replace("~", "~0").replace("/", "~1")


def windows(text: str) -> Iterator[tuple[int, int]]:
    """Sentence-like windows of at most WINDOW_CHARS, overlapping when a sentence is longer.

    Rules that need several words to co-occur search inside one window with simple patterns,
    which keeps matching linear in the text length.
    """
    start = 0
    for match in _BREAK.finditer(text):
        yield from _chunks(start, match.end())
        start = match.end()
    yield from _chunks(start, len(text))


def _chunks(start: int, end: int) -> Iterator[tuple[int, int]]:
    while end - start > WINDOW_CHARS:
        yield start, start + WINDOW_CHARS
        start += WINDOW_CHARS - WINDOW_OVERLAP
    if end > start:
        yield start, end


def window_matches(
    fields: Sequence[TextField], required: Sequence[re.Pattern[str]], anchor: re.Pattern[str]
) -> list[Evidence]:
    """Evidence for every window where all required patterns match, located at the anchor."""
    found: list[Evidence] = []
    seen: set[tuple[str, int]] = set()
    for field in fields:
        for view in field.views:
            for start, end in windows(view.text):
                window = view.text[start:end]
                if not all(pattern.search(window) for pattern in required):
                    continue
                match = anchor.search(window)
                if match:
                    _add(found, seen, field, view.span(start + match.start(), start + match.end()))
    return found


def pattern_matches(fields: Sequence[TextField], pattern: re.Pattern[str]) -> list[Evidence]:
    found: list[Evidence] = []
    seen: set[tuple[str, int]] = set()
    for field in fields:
        for view in field.views:
            for match in pattern.finditer(view.text):
                _add(found, seen, field, view.span(match.start(), match.end()))
    return found


def _add(
    found: list[Evidence], seen: set[tuple[str, int]], field: TextField, span: tuple[int, int]
) -> None:
    """Evidence at an original span, once per field and start offset across views."""
    if (field.path, span[0]) not in seen:
        seen.add((field.path, span[0]))
        found.append(evidence(field, *span))


def is_hidden(char: str) -> bool:
    """True for characters that are invisible or control characters when displayed."""
    if char in _VISIBLE_CONTROLS:
        return False
    category = unicodedata.category(char)
    return (
        category in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")
        or (category == "Zs" and char != " ")
        or char in _FILLERS
        or "\ufe00" <= char <= "\ufe0f"
        or "\U000e0100" <= char <= "\U000e01ef"
    )


def char_name(char: str) -> str:
    name = unicodedata.name(char, "")
    return f"[U+{ord(char):04X} {name}]" if name else f"[U+{ord(char):04X}]"


def visible(text: str) -> str:
    """Text safe to show: hidden characters replaced by their code point and name."""
    return "".join(char_name(c) if is_hidden(c) else c for c in text)


def evidence(field: TextField, start: int, end: int) -> Evidence:
    excerpt = visible(field.text[start:end])
    if len(excerpt) > MAX_EXCERPT_CHARS:
        excerpt = excerpt[:MAX_EXCERPT_CHARS] + "..."
    left = visible(field.text[max(0, start - CONTEXT_CHARS) : start])
    right = visible(field.text[end : end + CONTEXT_CHARS])
    return Evidence(
        field=field.path, text=excerpt, start=start, end=end, context=left + excerpt + right
    )


def json_evidence(path: str, value: Any) -> Evidence:
    """Evidence for a structural finding: compact JSON of the value, offsets cover the text."""
    text = visible(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    if len(text) > MAX_EXCERPT_CHARS:
        text = text[:MAX_EXCERPT_CHARS] + "..."
    return Evidence(field=path, text=text, start=0, end=len(text), context=text)


def describe_field(path: str) -> str:
    """Human wording for where evidence came from, for explanations."""
    if path in ("name", "title", "description"):
        return f"tool {path}"
    if path.startswith("annotations/"):
        return "annotations"
    if path.endswith("(property name)"):
        return "a parameter name"
    return "the input schema"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
