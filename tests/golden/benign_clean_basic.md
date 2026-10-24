# ToolGuard scan report

- Target: ` manifest `
- Scan ID: ` 00000000000000000000000000000000 `
- Started: 2026-01-01T00:00:00+00:00
- Finished: 2026-01-01T00:00:00+00:00
- Tools scanned: 2
- Versions: ToolGuard ` pinned `, rule set ` 1 `, report schema ` 1 `, prompt ` none `

## Verdict

**PASS**: No issues at or above the chosen threshold were detected by ToolGuard's checks. This is not a guarantee of safety.

How it was decided: pass: no finding reaches the fail_on threshold (high), and the highest severity (none) is below medium.

Rules only: AI review was not used.

## AI review

Status: disabled, reason disabled_by_request.

AI review was not used for this scan (turned off for this scan). Findings come from deterministic rules only.

- Tools reviewed by AI: 0
- Tools that qualified but were not reviewed: 0
- Tools skipped by triage: 0
- Tools checked by rules only: 2

Tools not reviewed by AI were checked by rules only.

## Summary

| Severity | Findings |
| --- | --- |
| critical | 0 |
| high | 0 |
| medium | 0 |
| low | 0 |
| info | 0 |

| Category | Findings |
| --- | --- |
| cross_tool | 0 |
| excessive_permissions | 0 |
| exfiltration | 0 |
| hidden_characters | 0 |
| injection | 0 |
| naming | 0 |
| structure | 0 |

## Findings

No findings.

## Tools scanned

- ` get_weather `: highest severity none, 0 findings
- ` convert_temperature `: highest severity none, 0 findings

## Method

ToolGuard checked each tool's name, title, description, annotations, and input schema with deterministic rules for prompt injection, data exfiltration wording, hidden characters, cross-tool manipulation, excessive permissions, naming problems, and unusual structure. It does not run the tools, read the server's code, or see what the tools return at run time. Rules detect known patterns and can miss reworded attacks (on a held-out set of new attacks they fully detected 2 of 11), so AI review matters for coverage.

A result without findings is not a guarantee of safety: ToolGuard only detects the patterns its checks cover.
