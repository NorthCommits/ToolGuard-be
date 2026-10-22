"""Hidden and look-alike character rules (TG-HID)."""

import unicodedata
from collections import Counter

from toolguard.core.models import Category, Evidence, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.text import TextField, char_name, describe_field, evidence, is_hidden, plural

# Explicit direction controls used to reorder displayed text (Trojan Source).
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
# Plain direction marks: legitimate right-to-left text uses them, so neither rule reports them.
DIRECTION_MARKS = frozenset("\u200e\u200f\u061c")
# Zero width joiner and the emoji variation selector are normal inside emoji sequences.
_EMOJI_GLUE = frozenset("\u200d\ufe0f")
# Scripts that are normally mixed within one name.
_SCRIPT_GROUPS = {"HIRAGANA": "HAN", "KATAKANA": "HAN", "KATAKANA-HIRAGANA": "HAN", "CJK": "HAN"}


def _runs(field: TextField, match) -> list[tuple[int, int]]:
    """Spans of consecutive characters in the field for which match(index) is true."""
    spans: list[tuple[int, int]] = []
    start = None
    for index in range(len(field.text) + 1):
        hit = index < len(field.text) and match(index)
        if hit and start is None:
            start = index
        elif not hit and start is not None:
            spans.append((start, index))
            start = None
    return spans


def _is_emoji_glue(text: str, index: int) -> bool:
    if text[index] not in _EMOJI_GLUE:
        return False
    before = text[index - 1] if index > 0 else ""
    return bool(before) and unicodedata.category(before) in ("So", "Sk")


def _char_hit(fields: list[TextField], match, label: str, why: str, action: str) -> Hit | None:
    found: list[Evidence] = []
    names: list[str] = []
    places: list[str] = []
    for field in fields:
        for start, end in _runs(field, lambda i, f=field: match(f.text, i)):
            found.append(evidence(field, start, end))
            names.extend(char_name(c) for c in field.text[start:end])
            places.append(describe_field(field.path))
    if not found:
        return None
    first = list(dict.fromkeys(names))[:3]
    where = ", ".join(dict.fromkeys(places))
    return Hit(
        evidence=found,
        explanation=(
            f"The {where} contains {plural(len(names), label)}, such as {', '.join(first)}. {why}"
        ),
        suggested_action=action,
    )


def check_invisible(tool: Tool, ctx: RuleContext) -> Hit | None:
    def match(text: str, index: int) -> bool:
        char = text[index]
        return (
            is_hidden(char)
            and char not in BIDI_CONTROLS
            and char not in DIRECTION_MARKS
            # Unusual spaces such as no-break space still render as space: shown by name in
            # evidence, but not reported on their own.
            and unicodedata.category(char) != "Zs"
            and not _is_emoji_glue(text, index)
        )

    return _char_hit(
        list(ctx.fields),
        match,
        "invisible character",
        "People reviewing the tool cannot see these characters, but the AI model still reads "
        "them, so they can hide text or make two names look identical.",
        "Remove the invisible characters, and treat the server as untrusted until the reason is "
        "clear.",
    )


def check_bidi(tool: Tool, ctx: RuleContext) -> Hit | None:
    return _char_hit(
        list(ctx.fields),
        lambda text, index: text[index] in BIDI_CONTROLS,
        "text direction control",
        "These characters change the order in which text is displayed, so what a person reads "
        "can differ from what the AI model reads.",
        "Remove the direction control characters and review the text as the model sees it.",
    )


def _script(char: str) -> str:
    first = unicodedata.name(char, "UNKNOWN").split(" ")[0]
    return _SCRIPT_GROUPS.get(first, first)


def check_mixed_script(tool: Tool, ctx: RuleContext) -> Hit | None:
    name = tool.name
    letters = [i for i, c in enumerate(name) if c.isalpha()]
    scripts = Counter(_script(name[i]) for i in letters)
    main = scripts.most_common(1)[0][0] if scripts else ""
    odd = [
        i
        for i in letters
        if _script(name[i]) != main or unicodedata.normalize("NFKC", name[i]) != name[i]
    ]
    if not odd:
        return None
    field = TextField("name", name)
    shown = ", ".join(dict.fromkeys(f"{name[i]} {char_name(name[i])}" for i in odd[:3]))
    return Hit(
        evidence=[evidence(field, i, i + 1) for i in odd],
        explanation=(
            f"The tool name mixes writing systems or uses look-alike letters ({shown}). A name "
            "like this can imitate another tool while looking the same to a person."
        ),
        suggested_action=(
            "Use one writing system for the tool name, and check whether it imitates a tool "
            "from another server."
        ),
    )


RULES = [
    Rule(
        "TG-HID-001",
        Category.HIDDEN_CHARACTERS,
        Severity.HIGH,
        "Zero-width or invisible characters in tool text",
        check_invisible,
    ),
    Rule(
        "TG-HID-002",
        Category.HIDDEN_CHARACTERS,
        Severity.HIGH,
        "Bidirectional control characters",
        check_bidi,
    ),
    Rule(
        "TG-HID-003",
        Category.HIDDEN_CHARACTERS,
        Severity.MEDIUM,
        "Mixed-script or look-alike characters in tool names",
        check_mixed_script,
    ),
]
