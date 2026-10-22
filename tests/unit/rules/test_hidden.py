import pytest
from rule_helpers import findings, fires, make_tool, prop, schema_with


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(description="Weather\u200b report."),
        make_tool(name="get\u200bweather"),
        make_tool(title="Get\u2060weather"),
        make_tool(description="Hidden \U000e0041\U000e0042 tag characters."),
        make_tool(description="Terminal escape \x1b[31m here."),
        make_tool(description="Lone surrogate \ud800 here."),
        make_tool(schema=schema_with(city=prop(description="City\u200b name."))),
    ],
)
def test_invisible_positive(tool) -> None:
    assert fires("TG-HID-001", tool)


@pytest.mark.parametrize(
    "description",
    [
        "Returns the weather for a city.",
        "Emoji sequences like \U0001f469\u200d\U0001f4bb and \u2764\ufe0f are fine.",
        "Distance is 10\u00a0km.",
        "Line one.\nLine two.\tTabbed.",
        "\u05e9\u05dc\u05d5\u05dd \u200f mark is plain right-to-left text.",
    ],
)
def test_invisible_negative(description: str) -> None:
    assert not fires("TG-HID-001", make_tool(description=description))


def test_invisible_evidence_names_the_character_and_keeps_offsets() -> None:
    description = "Weather\u200b report."
    [finding] = findings("TG-HID-001", make_tool(description=description))
    [evidence] = finding.evidence
    assert (evidence.field, evidence.start, evidence.end) == ("description", 7, 8)
    assert description[evidence.start : evidence.end] == "\u200b"
    assert evidence.text == "[U+200B ZERO WIDTH SPACE]"
    assert "\u200b" not in evidence.context
    assert "ZERO WIDTH SPACE" in finding.explanation


@pytest.mark.parametrize("char", ["\u202e", "\u202a", "\u2066", "\u2069"])
def test_bidi_positive(char: str) -> None:
    assert fires("TG-HID-002", make_tool(description=f"Checks access. {char}denied"))


@pytest.mark.parametrize(
    "description",
    ["Checks access.", "\u05e9\u05dc\u05d5\u05dd\u200f and \u0645\u0631\u062d\u0628\u0627\u061c."],
)
def test_bidi_negative(description: str) -> None:
    assert not fires("TG-HID-002", make_tool(description=description))


@pytest.mark.parametrize(
    "name", ["\u0455earch_files", "get_we\u0430ther", "\uff46etch", "read_\ufb01le"]
)
def test_mixed_script_positive(name: str) -> None:
    assert fires("TG-HID-003", make_tool(name=name))


@pytest.mark.parametrize(
    "name",
    [
        "get_weather",
        "\u043f\u043e\u0438\u0441\u043a",
        "\u5929\u6c17\u3092\u53d6\u5f97",
        "v2_tool_10",
    ],
)
def test_mixed_script_negative(name: str) -> None:
    assert not fires("TG-HID-003", make_tool(name=name))


def test_mixed_script_evidence_points_at_the_odd_letter() -> None:
    [finding] = findings("TG-HID-003", make_tool(name="get_we\u0430ther"))
    assert [(e.start, e.end) for e in finding.evidence] == [(6, 7)]
    assert "CYRILLIC SMALL LETTER A" in finding.explanation
