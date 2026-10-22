"""Exfiltration rules (TG-EXF): moving data or secrets out through the tool definition."""

import re

from toolguard.core.models import Category, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.text import describe_field, pattern_matches, window_matches

_I = re.IGNORECASE

SEND_VERB = re.compile(
    r"\b(?:send|sends|post|posts|upload|uploads|forward|forwards|transmit|transmits|exfiltrate"
    r"|submit|submits|copy|copies|leak|report|include|includes|bcc|cc|mirror|mirrors|sync|syncs"
    r"|replicate|replicates|share|shares)\b",
    _I,
)
# Data that a tool description has no business shipping elsewhere.
PRIVATE_DATA = re.compile(
    r"\b(?:conversation|chat\s{1,3}history|chat\s{1,3}log|previous\s{1,3}messages|all\s{1,3}messages"
    r"|message\s{1,3}history|system\s{1,3}prompt|context\s{1,3}window|file\s{1,3}contents?"
    r"|contents\s{1,3}of|secrets?|api\s{1,3}keys?|private\s{1,3}keys?|ssh\s{1,3}keys?"
    r"|access\s{1,3}tokens?|credentials?|passwords?|environment\s{1,3}variables?|env\s{1,3}vars?"
    r"|cookies?|session\s{1,3}data|user\s{1,3}data|personal\s{1,3}data)\b",
    _I,
)
EXTERNAL_TARGET = re.compile(
    r"\bhttps?://[^\s<>\"']{1,200}|\b[\w.+-]{1,64}@[\w-]{1,63}\.[\w.-]{1,190}"
    # A bare host name with a common public suffix, such as sync.example.net.
    r"|\b(?:[a-z0-9-]{1,63}\.){1,4}(?:com|net|org|io|dev|app|co|ai|xyz|info|biz|cloud|site)\b",
    _I,
)

SENSITIVE_PATH = re.compile(
    r"~/\.ssh\b|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|(?<![\w.])\.env\b|/etc/(?:passwd|shadow)\b"
    r"|\.aws/credentials\b|(?<![\w.])\.netrc\b|(?<![\w.])\.npmrc\b|\.git-credentials\b"
    r"|\.kube/config\b|\bwallet\.dat\b"
    r"|\bid\s(?:rsa|ed25519|ecdsa|dsa)\b"  # key file names after separators become spaces
    r"|~/\.config/[^\s'\"]{0,80}?(?:token|credential|secret|password)"
    r"|\b[\w-]{0,40}(?:token|tokens|credentials?|secrets?|passwords?)\.(?:json|ya?ml|txt|env|ini|toml)\b",
    _I,
)
ANY_DIRECTIVE = re.compile(
    r"\b(?:read|cat|open|access|retrieve|extract|collect|copy|include|attach|pass|send|forward"
    r"|upload|print|output|load|put)\b"
    r"|\b(?:contents?|text)\s{1,3}of\b",  # "the contents of ~/.ssh/id_rsa" asks for the file
    _I,
)
SECRET_NOUN = re.compile(
    r"\b(?:api|secret|private|access|ssh)\s{1,3}keys?\b|\bpasswords?\b|\bcredentials?\b"
    r"|\b(?:access|auth|authentication|session)\s{1,3}tokens?\b|\bseed\s{1,3}phrase\b",
    _I,
)
# Narrower verbs for secret nouns: docs legitimately say "pass your API key in the header".
SECRET_DIRECTIVE = re.compile(
    r"\b(?:read|extract|collect|copy|send|forward|upload|exfiltrate|retrieve|print|output|dump)\b",
    _I,
)

BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")
HEX_BLOB = re.compile(r"\b[0-9a-fA-F]{128,}\b")


def _where(evidence) -> str:
    return ", ".join(dict.fromkeys(describe_field(e.field) for e in evidence))


def check_send_external(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = window_matches(ctx.fields, [SEND_VERB, PRIVATE_DATA, EXTERNAL_TARGET], EXTERNAL_TARGET)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} tells the AI model to send private data, such as the "
            "conversation or credentials, to an outside address."
        ),
        suggested_action=(
            "Do not connect this server. Treat any data already shared with it as exposed."
        ),
    )


def check_secret_access(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = window_matches(ctx.fields, [SENSITIVE_PATH, ANY_DIRECTIVE], SENSITIVE_PATH)
    found += window_matches(ctx.fields, [SECRET_NOUN, SECRET_DIRECTIVE], SECRET_NOUN)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} directs the AI model to secrets or sensitive files, such as "
            "private keys or credentials, that this tool should never need."
        ),
        suggested_action=(
            "Do not connect this server. Check whether any secrets were exposed and rotate them."
        ),
    )


def check_encoded_blob(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = pattern_matches(ctx.fields, BASE64_BLOB) + pattern_matches(ctx.fields, HEX_BLOB)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} contains a long encoded block. Encoded text can hide "
            "instructions or data from people reviewing the tool."
        ),
        suggested_action="Decode the block and check what it contains before trusting the tool.",
    )


RULES = [
    Rule(
        "TG-EXF-001",
        Category.EXFILTRATION,
        Severity.CRITICAL,
        "Instruction to send data to an external address",
        check_send_external,
    ),
    Rule(
        "TG-EXF-002",
        Category.EXFILTRATION,
        Severity.HIGH,
        "References to secrets, keys, credentials, or sensitive files in directives",
        check_secret_access,
    ),
    Rule(
        "TG-EXF-003",
        Category.EXFILTRATION,
        Severity.MEDIUM,
        "Large encoded blobs in tool text",
        check_encoded_blob,
    ),
]
