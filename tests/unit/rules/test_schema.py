import pytest
from rule_helpers import MISSING, findings, fires, make_tool, prop, schema_with


@pytest.mark.parametrize(
    "schema",
    [
        schema_with(command=prop()),
        schema_with(sql_query=prop()),
        schema_with(shellScript={"description": "Script to run."}),
        schema_with(commands=prop(type="array", items={"type": "string"})),
        schema_with(options=prop(type="object", properties={"code": prop()})),
    ],
)
def test_execution_like_positive(schema) -> None:
    assert fires("TG-SCH-001", make_tool(schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        schema_with(command=prop(enum=["uptime"])),
        schema_with(commands=prop(type="array", items={"type": "string", "enum": ["a"]})),
        schema_with(path=prop()),
        schema_with(query=prop()),
        schema_with(code=prop(type="integer")),
    ],
)
def test_execution_like_negative(schema) -> None:
    assert not fires("TG-SCH-001", make_tool(schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        schema_with(filePath=prop()),
        schema_with(target_url={"description": "Where to go."}),
        schema_with(filename=prop()),
        schema_with(paths=prop(type="array", items={"type": "string"})),
        schema_with(host=prop(type=["string", "null"])),
    ],
)
def test_locator_positive(schema) -> None:
    assert fires("TG-SCH-006", make_tool(schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        schema_with(path=prop(pattern="^reports/[a-z]{1,20}$")),
        schema_with(url=prop(const="https://example.com")),
        schema_with(paths=prop(type="array", items={"type": "string", "pattern": "^/tmp/"})),
        schema_with(query=prop()),
        schema_with(command=prop()),
        schema_with(city=prop()),
    ],
)
def test_locator_negative(schema) -> None:
    assert not fires("TG-SCH-006", make_tool(schema=schema))


def test_free_form_explanation_names_the_parameter() -> None:
    [finding] = findings("TG-SCH-001", make_tool(schema=schema_with(command=prop())))
    assert "command" in finding.explanation
    assert finding.evidence[0].field == "input_schema/properties/command"


@pytest.mark.parametrize(
    "schema",
    [
        MISSING,
        schema_with(city={"type": "string"}),
        schema_with(city={"description": "City."}),
        schema_with(city=prop(description="   ")),
        schema_with(city=True),
    ],
)
def test_missing_type_or_description_positive(schema) -> None:
    assert fires("TG-SCH-002", make_tool(schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        schema_with(city=prop()),
        schema_with(item={"$ref": "#/$defs/item", "description": "An item."}),
        schema_with(value={"anyOf": [{"type": "string"}], "description": "A value."}),
        {"type": "object"},
    ],
)
def test_missing_type_or_description_negative(schema) -> None:
    assert not fires("TG-SCH-002", make_tool(schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}, "additionalProperties": True},
        schema_with(inner=prop(type="object", additionalProperties={})),
    ],
)
def test_additional_properties_positive(schema) -> None:
    assert fires("TG-SCH-003", make_tool(schema=schema))


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}},
        {"type": "object", "additionalProperties": False},
        {"type": "object", "additionalProperties": {"type": "string"}},
    ],
)
def test_additional_properties_negative(schema) -> None:
    assert not fires("TG-SCH-003", make_tool(schema=schema))


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(name="delete_records"),
        make_tool(name="cleanup", description="Permanently deletes old files."),
        make_tool(name="dropTable"),
        make_tool(name="delete_records", annotations={"readOnlyHint": "yes"}),
    ],
)
def test_destructive_without_hint_positive(tool) -> None:
    assert fires("TG-SCH-004", tool)


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(name="delete_records", annotations={"destructiveHint": True}),
        make_tool(name="delete_records", annotations={"readOnlyHint": False}),
        make_tool(name="get_weather"),
        # "deleted" is not the word "delete", and the description deletes nothing.
        make_tool(name="list_deleted_items_count", description="Counts items in the trash."),
    ],
)
def test_destructive_without_hint_negative(tool) -> None:
    assert not fires("TG-SCH-004", tool)


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(name="delete_account", annotations={"readOnlyHint": True}),
        make_tool(name="purge_cache", annotations={"destructiveHint": False}),
        make_tool(name="dropTable", annotations={"readOnlyHint": True, "destructiveHint": True}),
    ],
)
def test_contradicting_hint_positive(tool) -> None:
    assert fires("TG-SCH-005", tool)


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(name="delete_account", annotations={"destructiveHint": True}),
        make_tool(name="get_weather", annotations={"readOnlyHint": True}),
        make_tool(name="create_item", annotations={"destructiveHint": False}),
        make_tool(name="delete_account"),
        # Description verbs alone never decide: the name has no destructive word.
        make_tool(
            name="cleanup",
            description="Removes every cached entry.",
            annotations={"readOnlyHint": False, "destructiveHint": False},
        ),
        # Playwright: removes a visual highlight, not data.
        make_tool(
            name="browser_hide_highlight",
            description="Remove element highlights added by browser_highlight.",
            annotations={"readOnlyHint": True},
        ),
        make_tool(name="send_message", annotations={"readOnlyHint": True}),
    ],
)
def test_contradicting_hint_negative(tool) -> None:
    assert not fires("TG-SCH-005", tool)


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(name="send_message", annotations={"readOnlyHint": True}),
        # Playwright: saves a PDF file, but claims to be read-only.
        make_tool(name="browser_pdf_save", annotations={"readOnlyHint": True}),
        make_tool(name="updateProfile", annotations={"readOnlyHint": True}),
    ],
)
def test_read_only_modifier_positive(tool) -> None:
    assert fires("TG-SCH-007", tool)


@pytest.mark.parametrize(
    "tool",
    [
        make_tool(name="send_message", annotations={"readOnlyHint": False}),
        make_tool(name="create_item", annotations={"destructiveHint": False}),
        make_tool(name="get_weather", annotations={"readOnlyHint": True}),
        make_tool(
            name="browser_hide_highlight",
            description="Remove element highlights added by browser_highlight.",
            annotations={"readOnlyHint": True},
        ),
        # A destructive name is TG-SCH-005's case, not this one.
        make_tool(name="delete_account", annotations={"readOnlyHint": True}),
        make_tool(name="saved_searches", description="Lists saved searches."),
    ],
)
def test_read_only_modifier_negative(tool) -> None:
    assert not fires("TG-SCH-007", tool)


def test_contradicting_hint_evidence_includes_the_claim() -> None:
    tool = make_tool(name="delete_account", annotations={"readOnlyHint": True})
    [finding] = findings("TG-SCH-005", tool)
    assert [e.field for e in finding.evidence] == ["name", "annotations"]
    assert "readOnlyHint true" in finding.explanation


def test_function_parameter_is_execution_like() -> None:
    assert fires("TG-SCH-001", make_tool(schema=schema_with(function=prop())))
    assert not fires("TG-SCH-001", make_tool(schema=schema_with(function=prop(enum=["sum"]))))
