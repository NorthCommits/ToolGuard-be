"""Injection rules (TG-INJ): text in tool definitions aimed at steering the AI model.

Patterns use literal words joined by single bounded whitespace runs, and multi-part checks run
inside short windows (text.window_matches), so matching stays linear in the text length.
"""

import re

from toolguard.core.models import Category, Severity, Tool
from toolguard.rules.base import Hit, Rule, RuleContext
from toolguard.rules.text import describe_field, pattern_matches, window_matches

_I = re.IGNORECASE

INSTRUCTION_TAG = re.compile(
    r"<\s{0,3}/?\s{0,3}(?:important|system|instructions?|admin|secret|hidden|critical)\b[^<>]{0,40}>",
    _I,
)
MODEL_ADDRESSED = re.compile(
    r"\b(?:the\s{1,3})?(?:assistant|ai\s{1,3}assistant|ai|llm|language\s{1,3}model)\s{1,3}"
    r"(?:must|should|shall|needs\s{1,3}to|has\s{1,3}to|is\s{1,3}required\s{1,3}to)\b"
    r"|\byou\s{1,3}(?:must|should)\s{1,3}(?:always|never|first|silently|secretly|immediately)\b",
    _I,
)
BEFORE_USING_TOOL = re.compile(
    r"\bbefore\s{1,3}(?:using|calling|invoking|running)\s{1,3}(?:this|the)\s{1,3}(?:tool|function)\b",
    _I,
)
DECODE = re.compile(r"\b(?:decode|decrypt|deobfuscate|unescape|base64-decode)\b", _I)
OBEY = re.compile(r"\b(?:follow|execute|run|obey|apply|carry\s{1,3}out|do\s{1,3}what)\b", _I)
ASSISTANT_NOTE = re.compile(
    r"\b(?:note|message|instructions?)\s{1,3}(?:to|for)\s{1,3}(?:the\s{1,3})?"
    r"(?:assistants?|ai|llms?|agents?|models?)\b"
    r"|\b(?:assistants?|llms?)\s{0,3}:",
    _I,
)
IMPERATIVE = re.compile(
    r"\b(?:read|call|send|pass|include|fetch|open|access|run|execute|provide|copy|retrieve|load)\b",
    _I,
)

OVERRIDE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\s{1,3}(?:all\s{1,3}|any\s{1,3})?"
    r"(?:of\s{1,3})?(?:the\s{1,3}|your\s{1,3})?"
    r"(?:previous|prior|above|earlier|preceding|system|original|other|existing)\s{1,3}"
    r"(?:instructions?|prompts?|messages?|rules?|directions?|guidelines?|context)\b"
    r"|\b(?:do\s{1,3}not|don't)\s{1,3}follow\s{1,3}(?:the\s{1,3}|your\s{1,3}|any\s{1,3})?"
    r"(?:previous|prior|system|other|original)\s{1,3}instructions?\b"
    r"|\byour\s{1,3}(?:real|true|new|actual)\s{1,3}(?:task|instructions?|goal)\s{1,3}(?:is|are)\b",
    _I,
)

HIDE_FROM_USER = re.compile(
    r"\b(?:do\s{1,3}not|don't|never|without)\s{1,3}"
    r"(?:tell|telling|mention|mentioning|inform|informing|notify|notifying|reveal|revealing"
    r"|alert|alerting|show|showing|disclose|disclosing)\s{1,3}"
    r"(?:this|it|that|the\s{1,3}user|users|anyone|them)\b"
    r"|\b(?:keep|hide)\s{1,3}(?:this|it|that)\s{1,3}(?:secret\s{1,3}|hidden\s{1,3})?from\s{1,3}"
    r"(?:the\s{1,3})?users?\b"
    r"|\bwithout\s{1,3}(?:the\s{1,3})?user'?s?\s{1,3}(?:knowledge|knowing|noticing|awareness|consent)\b"
    r"|\bthe\s{1,3}user\s{1,3}(?:must|should)\s{1,3}not\s{1,3}(?:know|see|be\s{1,3}told|notice)\b"
    # Stealth adverbs before an action, in any form of the category.
    r"|\b(?:silently|quietly|discreetly|secretly|covertly|invisibly)\s{1,3}"
    r"(?:send|read|call|include|add|attach|forward|copy|upload|run|execute|collect|store|log"
    r"|record|share)\b"
    # "no need to tell / bring this up with the user": telling the user is optional.
    r"|\b(?:no|not\s{1,3}any)\s{1,3}need\s{1,3}to\s{1,3}(?:tell|mention|inform|notify|alert"
    r"|bring\s{1,3}(?:this|it|that)\s{1,3}up)\b"
    r"|\b(?:not\s{1,3}necessary|unnecessary)\s{1,3}to\s{1,3}(?:tell|mention|inform|notify)\b"
    r"|\bwithout\s{1,3}letting\s{1,3}(?:the\s{1,3})?(?:user|them|anyone)\s{1,3}know\b",
    _I,
)

# TG-INJ-004: the model is told its own earlier guidance no longer applies. Narrow on purpose:
# both parts must appear in one sentence, and the first part must address the model ("you")
# about instructions, rules, or restrictions it was given. Product changelogs ("this version
# removes the old limit") do not address the model and are not reported.
EARLIER_GUIDANCE = re.compile(
    r"\byou\s{1,3}(?:were|have\s{1,3}been|had\s{1,3}been)\s{1,3}"
    r"(?:previously\s{1,3}|originally\s{1,3}|earlier\s{1,3})?"
    r"(?:told|advised|instructed|trained|configured|forbidden|restricted|not\s{1,3}allowed)\b"
    r"|\b(?:originally|previously|earlier)\s{1,3}you\s{1,3}"
    r"(?:did\s{1,3}not|didn't|could\s{1,3}not|couldn't|were|had|should)\b"
    r"|\byour\s{1,3}(?:previous|earlier|original|prior)\s{1,3}"
    r"(?:instructions?|rules?|restrictions?|guidelines?|guidance|policy|policies)\b",
    _I,
)
NO_LONGER_APPLIES = re.compile(
    r"\b(?:now|no\s{1,3}longer|anymore|any\s{1,3}more|(?:does|do)\s{1,3}not\s{1,3}apply"
    r"|(?:is|are|has\s{1,3}been|have\s{1,3}been)\s{1,3}(?:lifted|removed|revoked|replaced))\b",
    _I,
)


def _where(evidence) -> str:
    return ", ".join(dict.fromkeys(describe_field(e.field) for e in evidence))


def check_directive(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = (
        pattern_matches(ctx.fields, INSTRUCTION_TAG)
        + pattern_matches(ctx.fields, MODEL_ADDRESSED)
        + window_matches(ctx.fields, [BEFORE_USING_TOOL, IMPERATIVE], BEFORE_USING_TOOL)
        + window_matches(ctx.fields, [DECODE, OBEY], DECODE)
        + window_matches(ctx.fields, [ASSISTANT_NOTE, IMPERATIVE], ASSISTANT_NOTE)
    )
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} contains instructions addressed to the AI model rather than a "
            "description of what the tool does. The model may follow them without the user "
            "knowing."
        ),
        suggested_action=(
            "Remove the instructions, and do not connect the server until the publisher explains "
            "them."
        ),
    )


def check_override(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = pattern_matches(ctx.fields, OVERRIDE)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} tells the AI model to ignore or replace its earlier "
            "instructions. This is a common way to take over an agent."
        ),
        suggested_action="Do not connect this server. Report the text to the publisher.",
    )


def check_hide_from_user(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = pattern_matches(ctx.fields, HIDE_FROM_USER)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} asks the AI model to keep something from the user. A tool has "
            "no legitimate reason to hide its actions from the person using it."
        ),
        suggested_action="Do not connect this server until the hidden behavior is explained.",
    )


def check_guidance_lifted(tool: Tool, ctx: RuleContext) -> Hit | None:
    found = window_matches(ctx.fields, [EARLIER_GUIDANCE, NO_LONGER_APPLIES], EARLIER_GUIDANCE)
    if not found:
        return None
    return Hit(
        evidence=found,
        explanation=(
            f"The {_where(found)} tells the AI model that guidance it was given earlier no longer "
            "applies. Even when meant kindly, this wording can override the user's or the "
            "client's own instructions."
        ),
        suggested_action=(
            "Review whether the tool needs this wording. Describe what the tool does instead of "
            "telling the model what it may now do."
        ),
    )


RULES = [
    Rule(
        "TG-INJ-001",
        Category.INJECTION,
        Severity.HIGH,
        "Directive text aimed at the model",
        check_directive,
    ),
    Rule(
        "TG-INJ-002",
        Category.INJECTION,
        Severity.HIGH,
        "Phrasing that tries to override or disregard prior instructions",
        check_override,
    ),
    Rule(
        "TG-INJ-003",
        Category.INJECTION,
        Severity.HIGH,
        "Instructions to hide actions or information from the user",
        check_hide_from_user,
    ),
    Rule(
        "TG-INJ-004",
        Category.INJECTION,
        Severity.MEDIUM,
        "Text telling the model that its earlier guidance or restrictions no longer apply",
        check_guidance_lifted,
    ),
]
