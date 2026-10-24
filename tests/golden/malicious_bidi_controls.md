# ToolGuard scan report

- Target: ` manifest `
- Scan ID: ` 00000000000000000000000000000000 `
- Started: 2026-01-01T00:00:00+00:00
- Finished: 2026-01-01T00:00:00+00:00
- Tools scanned: 1
- Versions: ToolGuard ` pinned `, rule set ` 1 `, report schema ` 1 `, prompt ` none `

## Verdict

**FAIL**: Findings at or above the chosen threshold were detected.

How it was decided: fail: at least one finding is at or above the fail_on threshold (high).

Rules only: AI review was not used.

## AI review

Status: disabled, reason disabled_by_request.

AI review was not used for this scan (turned off for this scan). Findings come from deterministic rules only.

- Tools reviewed by AI: 0
- Tools that qualified but were not reviewed: 0
- Tools skipped by triage: 0
- Tools checked by rules only: 1

Tools not reviewed by AI were checked by rules only.

## Summary

| Severity | Findings |
| --- | --- |
| critical | 0 |
| high | 1 |
| medium | 0 |
| low | 0 |
| info | 0 |

| Category | Findings |
| --- | --- |
| cross_tool | 0 |
| excessive_permissions | 0 |
| exfiltration | 0 |
| hidden_characters | 1 |
| injection | 0 |
| naming | 0 |
| structure | 0 |

## Findings

### HIGH: TG-HID-002 on tool ` check_access `

- Category: hidden_characters
- Tool: ` check_access ` (tool 1 in the manifest)
- Rule: TG-HID-002, source: rule

Evidence 1 of 2, field ` description `, characters 43 to 44:

```
[U+202E RIGHT-TO-LEFT OVERRIDE]
```

Context:

```
[... 3 more characters]cks whether a user may open a document. [U+202E RIGHT-TO-LEFT OVERRIDE]denied = true[U+202C POP DIRECTIONAL FORMATTING]
```

Evidence 2 of 2, field ` description `, characters 57 to 58:

```
[U+202C POP DIRECTIONAL FORMATTING]
```

Context:

```
[... 17 more characters]user may open a document. [U+202E RIGHT-TO-LEFT OVERRIDE]denied = true[U+202C POP DIRECTIONAL FORMATTING]
```

Explanation: The tool description contains 2 text direction controls\, such as \[U\+202E RIGHT\-TO\-LEFT OVERRIDE\]\, \[U\+202C POP DIRECTIONAL FORMATTING\]\. These characters change the order in which text is displayed\, so what a person reads can differ from what the AI model reads\.

Suggested action: Remove the direction control characters and review the text as the model sees it\.

## Tools scanned

- ` check_access `: highest severity high, 1 finding

## Method

ToolGuard checked each tool's name, title, description, annotations, and input schema with deterministic rules for prompt injection, data exfiltration wording, hidden characters, cross-tool manipulation, excessive permissions, naming problems, and unusual structure. It does not run the tools, read the server's code, or see what the tools return at run time.

A result without findings is not a guarantee of safety: ToolGuard only detects the patterns its checks cover.
