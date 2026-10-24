# ToolGuard scan report

- Target: ` manifest `
- Scan ID: ` 00000000000000000000000000000000 `
- Started: 2026-01-01T00:00:00+00:00
- Finished: 2026-01-01T00:00:00+00:00
- Tools scanned: 4
- Versions: ToolGuard ` pinned `, rule set ` 1 `, report schema ` 1 `, prompt ` none `

## Verdict

**REVIEW**: Some findings need human review before this server is trusted.

How it was decided: review: no finding reaches the fail_on threshold (high), and the highest severity (medium) is medium or above.

Rules only: AI review was not used.

## AI review

Status: disabled, reason disabled_by_request.

AI review was not used for this scan (turned off for this scan). Findings come from deterministic rules only.

- Tools reviewed by AI: 0
- Tools that qualified but were not reviewed: 0
- Tools skipped by triage: 0
- Tools checked by rules only: 4

Tools not reviewed by AI were checked by rules only.

## Summary

| Severity | Findings |
| --- | --- |
| critical | 0 |
| high | 0 |
| medium | 1 |
| low | 5 |
| info | 0 |

| Category | Findings |
| --- | --- |
| cross_tool | 0 |
| excessive_permissions | 6 |
| exfiltration | 0 |
| hidden_characters | 0 |
| injection | 0 |
| naming | 0 |
| structure | 0 |

## Findings

### MEDIUM: TG-SCH-001 on tool ` run_command `

- Category: excessive_permissions
- Tool: ` run_command ` (tool 1 in the manifest)
- Rule: TG-SCH-001, source: rule

Evidence 1 of 1, field ` input_schema/properties/command `, characters 0 to 49:

```
{"description":"Command to run.","type":"string"}
```

Context:

```
{"description":"Command to run.","type":"string"}
```

Explanation: The parameter named command accepts any text that looks like it will be run or evaluated\, such as a command\, code\, or SQL\.

Suggested action: Restrict the parameter with an enum or a pattern\, and validate it on the server\.

### LOW: TG-SCH-002 on tool ` write_file `

- Category: excessive_permissions
- Tool: ` write_file ` (tool 2 in the manifest)
- Rule: TG-SCH-002, source: rule

Evidence 1 of 1, field ` input_schema/properties/content `, characters 0 to 2:

```
{}
```

Context:

```
{}
```

Explanation: The parameter named content has no type or no description\, so the AI model has to guess what to send\.

Suggested action: Give every parameter a type and a short description\.

### LOW: TG-SCH-003 on tool ` write_file `

- Category: excessive_permissions
- Tool: ` write_file ` (tool 2 in the manifest)
- Rule: TG-SCH-003, source: rule

Evidence 1 of 1, field ` input_schema/additionalProperties `, characters 0 to 4:

```
true
```

Context:

```
true
```

Explanation: The schema explicitly accepts parameters it does not define\, so callers can pass anything alongside the documented inputs\.

Suggested action: Set additionalProperties to false unless extra fields are required\.

### LOW: TG-SCH-006 on tool ` write_file `

- Category: excessive_permissions
- Tool: ` write_file ` (tool 2 in the manifest)
- Rule: TG-SCH-006, source: rule

Evidence 1 of 1, field ` input_schema/properties/path `, characters 0 to 44:

```
{"description":"File path.","type":"string"}
```

Context:

```
{"description":"File path.","type":"string"}
```

Explanation: The parameter named path accepts any path\, file name\, or address\, so the tool can be pointed at anything the server can reach\.

Suggested action: Check that the server confines the value\, for example to allowed folders or hosts\, and describe the limit in the schema with a pattern or enum where possible\.

### LOW: TG-SCH-004 on tool ` delete_records `

- Category: excessive_permissions
- Tool: ` delete_records ` (tool 3 in the manifest)
- Rule: TG-SCH-004, source: rule

Evidence 1 of 1, field ` name `, characters 0 to 6:

```
delete
```

Context:

```
delete_records
```

Explanation: The tool delete\_records sounds destructive but declares no readOnlyHint or destructiveHint\, so clients cannot tell from its metadata whether it changes data\.

Suggested action: Set destructiveHint to true so clients can ask for confirmation\.

### LOW: TG-SCH-002 on tool ` get_status `

- Category: excessive_permissions
- Tool: ` get_status ` (tool 4 in the manifest)
- Rule: TG-SCH-002, source: rule

Evidence 1 of 1, field ` input_schema `, characters 0 to 0:

```
(no input schema)
```

Context:

```

```

Explanation: The tool has no input schema\, so nothing describes or limits what it accepts\.

Suggested action: Add an input schema with a type and description for each parameter\.

## Tools scanned

- ` run_command `: highest severity medium, 1 finding
- ` write_file `: highest severity low, 3 findings
- ` delete_records `: highest severity low, 1 finding
- ` get_status `: highest severity low, 1 finding

## Method

ToolGuard checked each tool's name, title, description, annotations, and input schema with deterministic rules for prompt injection, data exfiltration wording, hidden characters, cross-tool manipulation, excessive permissions, naming problems, and unusual structure. It does not run the tools, read the server's code, or see what the tools return at run time.

A result without findings is not a guarantee of safety: ToolGuard only detects the patterns its checks cover.
