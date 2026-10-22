"""Manifest ingestion: turn a JSON tool list into normalized Tool models (sdd.md section 5.1).

Accepted shapes:
- a bare array of tools;
- an object with a "tools" array and an optional "server_name" (an MCP tools/list result);
- a full JSON-RPC tools/list response, whose "result" holds that object.
Tool entries accept MCP field names (inputSchema) and ours (input_schema). Unknown fields are
ignored. All problems are collected and reported together; nothing is partially accepted.
"""

import hashlib
import json
import unicodedata
from typing import Any

from toolguard.core.errors import InvalidInput, PayloadTooLarge, Problem
from toolguard.core.models import Tool

DEFAULT_SERVER_NAME = "unknown"
# An explicit limit, so acceptance does not depend on the Python version's recursion limits.
MAX_NESTING_DEPTH = 64


def content_hash(
    name: str,
    title: str | None,
    description: str,
    input_schema: dict[str, Any],
    input_schema_missing: bool,
    annotations: dict[str, Any],
) -> str:
    """SHA-256 of the canonical tool content.

    Text is trimmed and the whole canonical JSON is NFC-normalized for hashing only. NFC (not
    NFKC) keeps look-alike and zero-width characters distinct, so a spoofed tool never shares a
    hash, and a cached verdict, with the clean one.
    """
    payload = {
        "name": name.strip(),
        "title": title.strip() if title is not None else None,
        "description": description.strip(),
        "input_schema": input_schema,
        "input_schema_missing": input_schema_missing,
        "annotations": annotations,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # surrogatepass: JSON allows lone surrogate escapes; keep them (a rule reports them).
    normalized = unicodedata.normalize("NFC", canonical)
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()


def build_tool(
    server_name: str,
    name: str,
    title: str | None = None,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
) -> Tool:
    """Build a Tool from already validated values. input_schema None means it was missing."""
    missing = input_schema is None
    schema = {} if missing else input_schema
    notes = annotations or {}
    return Tool(
        server_name=server_name,
        name=name,
        title=title,
        description=description,
        input_schema=schema,
        input_schema_missing=missing,
        annotations=notes,
        content_hash=content_hash(name, title, description, schema, missing, notes),
    )


def parse_manifest(
    raw: bytes, *, max_bytes: int, max_tools: int, server_name: str | None = None
) -> list[Tool]:
    """Parse manifest bytes into tools. server_name, when given, overrides the manifest's.

    Raises PayloadTooLarge for size and tool-count limits, InvalidInput for anything else.
    """
    if len(raw) > max_bytes:
        raise PayloadTooLarge(f"The manifest is larger than the {max_bytes} byte limit.")
    try:
        document = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant)
        if _depth_exceeds(document, MAX_NESTING_DEPTH):
            raise InvalidInput(
                f"The manifest is nested too deeply (more than {MAX_NESTING_DEPTH} levels)."
            )
        return _tools_from_document(document, max_tools, server_name)
    except UnicodeDecodeError:
        raise InvalidInput("The manifest must be UTF-8 encoded JSON.") from None
    except json.JSONDecodeError as exc:
        problem = Problem(reason=f"invalid JSON at line {exc.lineno}, column {exc.colno}")
        raise InvalidInput("The manifest is not valid JSON.", [problem]) from None
    except RecursionError:
        raise InvalidInput("The manifest is nested too deeply.") from None


def _depth_exceeds(document: Any, limit: int) -> bool:
    """True when JSON containers nest deeper than limit. Iterative, so it cannot overflow."""
    stack = [(document, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            if depth > limit:
                return True
            children = node.values() if isinstance(node, dict) else node
            stack.extend((child, depth + 1) for child in children)
    return False


def _reject_constant(name: str) -> None:
    raise InvalidInput(f"The manifest contains {name}, which is not valid JSON.")


def _tools_from_document(document: Any, max_tools: int, server_name: str | None) -> list[Tool]:
    entries, manifest_server_name = _unwrap(document)
    if len(entries) > max_tools:
        raise PayloadTooLarge(f"The manifest has more than the {max_tools} tool limit.")
    if not entries:
        raise InvalidInput("The manifest contains no tools.")

    resolved_server = server_name or manifest_server_name or DEFAULT_SERVER_NAME
    tools: list[Tool] = []
    problems: list[Problem] = []
    for index, entry in enumerate(entries):
        tool = _tool_from_entry(index, entry, resolved_server, problems)
        if tool is not None:
            tools.append(tool)
    if problems:
        raise InvalidInput("Some tool entries are invalid.", problems)
    return tools


def _unwrap(document: Any) -> tuple[list[Any], str | None]:
    """Return the tool entries and the manifest's server name, if any."""
    if isinstance(document, list):
        return document, None
    if not isinstance(document, dict):
        raise InvalidInput("The manifest must be a JSON array of tools or an object with tools.")
    if "tools" not in document and isinstance(document.get("result"), dict):
        document = document["result"]
    tools = document.get("tools")
    if not isinstance(tools, list):
        raise InvalidInput(
            "The manifest object must have a tools array.",
            [Problem(field="tools", reason="must be an array")],
        )
    server = document.get("server_name")
    if server is not None and (not isinstance(server, str) or not server.strip()):
        raise InvalidInput(
            "The manifest server_name is invalid.",
            [Problem(field="server_name", reason="must be a non-empty string")],
        )
    return tools, server


def _tool_from_entry(
    index: int, entry: Any, server_name: str, problems: list[Problem]
) -> Tool | None:
    """Validate one entry. Appends to problems and returns None when the entry is invalid."""
    if not isinstance(entry, dict):
        problems.append(Problem(index=index, reason="each tool must be a JSON object"))
        return None
    start = len(problems)

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append(Problem(index=index, field="name", reason="must be a non-empty string"))

    title = entry.get("title")
    if title is not None and not isinstance(title, str):
        problems.append(Problem(index=index, field="title", reason="must be a string"))

    description = entry.get("description")
    if description is None:
        description = ""
    elif not isinstance(description, str):
        problems.append(Problem(index=index, field="description", reason="must be a string"))

    schema = _schema_of(index, entry, problems)

    annotations = entry.get("annotations")
    if annotations is None:
        annotations = {}
    elif not isinstance(annotations, dict):
        problems.append(Problem(index=index, field="annotations", reason="must be a JSON object"))

    if len(problems) > start:
        return None
    return build_tool(server_name, name, title, description, schema, annotations)


def _schema_of(index: int, entry: dict[str, Any], problems: list[Problem]) -> dict | None:
    """The entry's schema, or None when it is missing. A present non-object is a problem."""
    present = [key for key in ("inputSchema", "input_schema") if key in entry]
    if not present:
        return None
    if len(present) == 2:
        problems.append(
            Problem(index=index, field="inputSchema", reason="give inputSchema or input_schema")
        )
        return None
    schema = entry[present[0]]
    if not isinstance(schema, dict):
        problems.append(Problem(index=index, field=present[0], reason="must be a JSON object"))
        return None
    return schema
