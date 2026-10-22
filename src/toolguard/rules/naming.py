"""Naming rules (TG-NAM): colliding names and names that imitate other tools."""

import re
import unicodedata

from toolguard.core.models import Category, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.text import TextField, evidence, visible

# Chosen from tool names in the official MCP reference servers (filesystem, fetch, git,
# GitHub) and common agent examples. Kept short on purpose: every name here is a chance for
# a false positive on a server that has a legitimately similar tool.
COMMON_TOOL_NAMES = (
    "read_file",
    "write_file",
    "edit_file",
    "list_directory",
    "search_files",
    "fetch",
    "send_email",
    "send_message",
    "create_issue",
    "run_command",
    "execute_command",
    "query_database",
)
MIN_COMPARE_LENGTH = 6
# Look-alike characters folded together before comparing. Small on purpose.
_CONFUSABLES = str.maketrans(
    {
        "0": "o", "1": "l", "i": "l", "|": "l", "5": "s", "$": "s", "3": "e", "@": "a",
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
        "\u0443": "y", "\u0445": "x", "\u0455": "s", "\u0456": "l", "\u0458": "j",
        "\u03bf": "o", "\u03b1": "a", "\u03bd": "v", "\u03c1": "p",
    }
)  # fmt: skip
_VERSION_SUFFIX = re.compile(r"(?:[_.-]?v?\d{1,6}){1,3}$")
_TOKEN_SPLIT = re.compile(r"[_.\-\s]")


def _fold(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _skeleton(name: str) -> str:
    return _fold(name).translate(_CONFUSABLES).replace("rn", "m").replace("vv", "w")


def _base(name: str) -> str:
    """Name without a version or numeric suffix and a trailing plural s."""
    return _VERSION_SUFFIX.sub("", _fold(name)).removesuffix("s")


def _token_bounds(name: str, index: int) -> tuple[int, int]:
    start = max((m.end() for m in _TOKEN_SPLIT.finditer(name, 0, index)), default=0)
    end_match = _TOKEN_SPLIT.search(name, index)
    return start, end_match.start() if end_match else len(name)


def _imitates(name: str, other: str) -> bool:
    """True for transpositions, single substitutions, and look-alike characters only."""
    a, b = _fold(name), _fold(other)
    if a == b or _base(a) == _base(b):
        return False  # same tool, or a plural or versioned variant
    if _skeleton(a) == _skeleton(b):
        return True
    if len(a) != len(b) or len(a) < MIN_COMPARE_LENGTH:
        return False
    diffs = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
        i = diffs[0]
        return a[i] == b[i + 1] and a[i + 1] == b[i]
    if len(diffs) == 1:
        i = diffs[0]
        if a[i].isdigit() and b[i].isdigit():
            return False
        start, end = _token_bounds(a, i)
        # A changed first letter (get_user, set_user) usually means a different action.
        return i != start and end - start >= 4
    return False


def check_duplicate(tool: Tool, ctx: RuleContext) -> Hit | None:
    folded = _fold(tool.name)
    count = sum(1 for other in ctx.all_tools if _fold(other.name) == folded)
    if count < 2:
        return None
    return Hit(
        evidence=[evidence(TextField("name", tool.name), 0, len(tool.name))],
        explanation=(
            f"{count} tools in this scan are named {visible(tool.name)} (ignoring case). The AI "
            "model cannot tell which one it is calling."
        ),
        suggested_action="Give each tool a unique name.",
    )


def check_imitation(tool: Tool, ctx: RuleContext) -> Hit | None:
    candidates = dict.fromkeys([*COMMON_TOOL_NAMES, *(t.name for t in ctx.all_tools)])
    for other in candidates:
        if other != tool.name and _imitates(tool.name, other):
            return Hit(
                evidence=[evidence(TextField("name", tool.name), 0, len(tool.name))],
                explanation=(
                    f"The tool name {visible(tool.name)} differs from {visible(other)} by a "
                    "swapped, changed, or look-alike character. It may be trying to be called "
                    "in place of that tool."
                ),
                suggested_action=(
                    "Check whether this server should provide a tool with this name, and prefer "
                    "the original tool's server."
                ),
            )
    return None


RULES = [
    Rule(
        "TG-NAM-001",
        Category.NAMING,
        Severity.MEDIUM,
        "Duplicate or colliding tool names within a scan",
        check_duplicate,
    ),
    Rule(
        "TG-NAM-002",
        Category.NAMING,
        Severity.MEDIUM,
        "Names that closely imitate common tool names",
        check_imitation,
    ),
]
