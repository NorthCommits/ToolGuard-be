import pytest
from rule_helpers import fires, make_tool


def test_duplicate_positive() -> None:
    assert fires("TG-NAM-001", make_tool(name="search"), make_tool(name="search"))
    assert fires("TG-NAM-001", make_tool(name="Search"), make_tool(name="search"))


def test_duplicate_negative() -> None:
    assert not fires("TG-NAM-001", make_tool(name="search"), make_tool(name="search_docs"))


@pytest.mark.parametrize(
    ("name", "others"),
    [
        ("send_emial", []),  # transposition of a common name
        ("read_fi1e", []),  # digit 1 for letter l
        ("\u0455earch_files", []),  # Cyrillic dze for s
        ("create_invoise", ["create_invoice"]),  # substitution inside a word, same scan
    ],
)
def test_imitation_positive(name: str, others: list[str]) -> None:
    assert fires("TG-NAM-002", make_tool(name=name), *(make_tool(name=o) for o in others))


@pytest.mark.parametrize(
    ("name", "others"),
    [
        ("get_user", ["get_users"]),  # plural
        ("list_items", ["list_items_v2"]),  # version suffix
        ("get_user", ["set_user"]),  # different action verb
        ("tool_1", ["tool_2"]),  # numeric suffix
        ("read_file", []),  # exact common name
        ("fetch", ["fetcj"]),  # too short to compare
        ("weather_report", ["weather_reports"]),
    ],
)
def test_imitation_negative(name: str, others: list[str]) -> None:
    assert not fires("TG-NAM-002", make_tool(name=name), *(make_tool(name=o) for o in others))
