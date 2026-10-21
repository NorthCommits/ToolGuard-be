import json
from typing import Any

import pytest

from toolguard.core.errors import InvalidInput, PayloadTooLarge
from toolguard.ingest.manifest import build_tool, parse_manifest

SCHEMA = {"type": "object", "properties": {"city": {"type": "string"}}}


def tool_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "get_weather",
        "description": "Weather.",
        "inputSchema": SCHEMA,
    }
    entry.update(overrides)
    return entry


def parse(document: Any, **kwargs: Any) -> list:
    raw = document if isinstance(document, bytes) else json.dumps(document).encode()
    kwargs.setdefault("max_bytes", 1_000_000)
    kwargs.setdefault("max_tools", 500)
    return parse_manifest(raw, **kwargs)


def problems_of(exc: pytest.ExceptionInfo) -> set[tuple]:
    return {(p.index, p.field) for p in exc.value.problems}


# Accepted shapes


def test_bare_array() -> None:
    tools = parse([tool_entry()])
    assert [t.name for t in tools] == ["get_weather"]
    assert tools[0].server_name == "unknown"
    assert tools[0].input_schema == SCHEMA


def test_object_with_server_name() -> None:
    tools = parse({"server_name": "weather", "tools": [tool_entry()]})
    assert tools[0].server_name == "weather"


def test_explicit_server_name_overrides_manifest() -> None:
    tools = parse({"server_name": "weather", "tools": [tool_entry()]}, server_name="mine")
    assert tools[0].server_name == "mine"


def test_mcp_tools_list_result_and_jsonrpc_response() -> None:
    result = {"tools": [tool_entry(title="Get weather")], "nextCursor": None}
    for document in (result, {"jsonrpc": "2.0", "id": 1, "result": result}):
        tools = parse(document)
        assert tools[0].title == "Get weather"


def test_snake_case_schema_name_is_accepted() -> None:
    entry = tool_entry()
    entry["input_schema"] = entry.pop("inputSchema")
    assert parse([entry])[0].input_schema == SCHEMA


def test_unknown_fields_are_ignored() -> None:
    tools = parse([tool_entry(outputSchema={"type": "object"}, _meta={"x": 1})])
    assert tools[0].name == "get_weather"


def test_optional_fields_default() -> None:
    tool = parse([{"name": "ping", "inputSchema": {}}])[0]
    assert tool.description == ""
    assert tool.title is None
    assert tool.annotations == {}
    assert tool.input_schema_missing is False


def test_missing_schema_becomes_empty_and_is_flagged() -> None:
    tool = parse([{"name": "ping"}])[0]
    assert tool.input_schema == {}
    assert tool.input_schema_missing is True


def test_duplicate_names_and_hidden_characters_are_accepted() -> None:
    tools = parse([tool_entry(), tool_entry(), tool_entry(name="get​_weather")])
    assert len(tools) == 3
    assert tools[2].name == "get​_weather"


def test_original_text_is_kept_unchanged() -> None:
    description = "  Weather.​ <b>x</b>\n"
    tool = parse([tool_entry(description=description)])[0]
    assert tool.description == description


# Rejected input


def test_all_entry_problems_are_reported_and_nothing_is_accepted() -> None:
    document = [
        tool_entry(),
        tool_entry(name=""),
        "not an object",
        tool_entry(description=5, annotations=[]),
        tool_entry(inputSchema="string"),
        tool_entry(title=3),
        tool_entry(input_schema=SCHEMA),
        tool_entry(inputSchema=None),
    ]
    with pytest.raises(InvalidInput) as exc:
        parse(document)
    assert problems_of(exc) == {
        (1, "name"),
        (2, None),
        (3, "description"),
        (3, "annotations"),
        (4, "inputSchema"),
        (5, "title"),
        (6, "inputSchema"),
        (7, "inputSchema"),
    }


@pytest.mark.parametrize(
    "document",
    [
        {"tools": "x"},
        {"server_name": "s"},
        {"server_name": "", "tools": [tool_entry()]},
        "just a string",
        [],
        {"tools": []},
    ],
)
def test_bad_top_level_shape(document: Any) -> None:
    with pytest.raises(InvalidInput):
        parse(document)


@pytest.mark.parametrize(
    "raw",
    [b"{not json", b'[{"name": "a", "x": NaN}]', b'[{"name": "a", "x": Infinity}]', b"\xff\xfe"],
)
def test_invalid_json_or_encoding(raw: bytes) -> None:
    with pytest.raises(InvalidInput):
        parse(raw)


def test_deep_nesting_is_rejected_cleanly() -> None:
    with pytest.raises(InvalidInput, match="nested too deeply"):
        parse(b"[" * 100_000 + b"]" * 100_000, max_bytes=1_000_000)


def test_byte_limit() -> None:
    raw = json.dumps([tool_entry()]).encode()
    with pytest.raises(PayloadTooLarge):
        parse(raw, max_bytes=len(raw) - 1)
    assert parse(raw, max_bytes=len(raw))


def test_tool_count_limit() -> None:
    with pytest.raises(PayloadTooLarge):
        parse([tool_entry()] * 3, max_tools=2)
    assert len(parse([tool_entry()] * 2, max_tools=2)) == 2


def test_error_messages_do_not_echo_scanned_content() -> None:
    secret_like = "SCANNED-CONTENT-MARKER"
    with pytest.raises(InvalidInput) as exc:
        parse([tool_entry(name=secret_like, description=5)])
    assert secret_like not in str(exc.value)
    assert all(secret_like not in p.reason for p in exc.value.problems)


# Content hash


def test_hash_ignores_key_order_and_outer_whitespace() -> None:
    a = build_tool("s", "t", None, "Weather.", {"a": 1, "b": {"c": 2, "d": 3}})
    b = build_tool("s", " t ", None, "  Weather.\n", {"b": {"d": 3, "c": 2}, "a": 1})
    assert a.content_hash == b.content_hash


def test_hash_is_stable_across_unicode_composition() -> None:
    composed = build_tool("s", "t", None, "café")
    decomposed = build_tool("s", "t", None, "café")
    assert composed.content_hash == decomposed.content_hash


@pytest.mark.parametrize(
    "changed",
    [
        {"name": "t​"},
        {"name": "т"},  # Cyrillic look-alike of t
        {"description": "Weather.‮"},
        {"title": "Weather"},
        {"input_schema": {"type": "object"}},
        {"annotations": {"readOnlyHint": True}},
    ],
)
def test_hash_changes_when_content_changes(changed: dict[str, Any]) -> None:
    base: dict[str, Any] = {"server_name": "s", "name": "t", "description": "Weather."}
    assert build_tool(**base).content_hash != build_tool(**{**base, **changed}).content_hash


def test_hash_tells_missing_schema_from_empty_schema() -> None:
    missing = build_tool("s", "t", input_schema=None)
    empty = build_tool("s", "t", input_schema={})
    assert missing.input_schema == empty.input_schema == {}
    assert missing.content_hash != empty.content_hash


def test_hash_ignores_server_name() -> None:
    assert build_tool("a", "t").content_hash == build_tool("b", "t").content_hash
