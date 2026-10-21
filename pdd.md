# ToolGuard: Product Design Document (PDD)

Status: Draft v0.1
Companion documents: prd.md (what and why), architecture.md (system shape), sdd.md (detailed software design)

## 1. Purpose of This Document

The PRD defines what ToolGuard must do. This document defines how it should feel to use: the experience of the API, the report, the future frontend (ToolGuard-fe), and the wording of every message a user will read. The HTTP API is the only interface of the product; there is no command line tool. It exists so that the product is coherent and trustworthy before any interface is built.

## 2. Design Principles

1. Evidence over verdicts. Every finding shows the exact text that triggered it and says why it matters. A user should never have to trust a score without seeing the reason.
2. Low noise. A scanner that cries wolf gets ignored. Severity must mean something, and weak signals should be labeled as such.
3. Honest about limits. ToolGuard reports risk signals. It never claims a server is safe. A clean result is worded as "no issues detected by these checks".
4. Honest about AI. When AI review was used, skipped, or partial, the report says so plainly.
5. Fast first value. A new user should see a real result within five minutes, with no account and no setup beyond keys.
6. Automation friendly. Everything a person sees has a machine-readable equivalent, and API responses and error codes are predictable.
7. Calm tone. Messages are direct and non-alarmist. Security findings are serious without being dramatic.

## 3. Users and Their Jobs

| User | Job to be done | What they need from ToolGuard |
|---|---|---|
| AI or backend engineer adding a third-party MCP server | "Before I connect this server to my agent, tell me if anything looks dangerous." | Fast scan, clear findings, quick decision |
| Platform or security engineer | "Block risky MCP servers in our pipeline and tell me what changed." | A verdict and counts from the API that a CI step can act on, stable report format, change visibility later |
| Frontend developer (ToolGuard-fe) | "Build the ToolGuard interface without waiting on backend changes." | Stable versioned API, progress, history, pagination, filters, consistent errors |
| MCP server author | "Check my own tools before I publish, and keep a regression suite." | Fixtures, repeatable runs, specific fix guidance |
| Portfolio reviewer | "See that this project is thoughtfully built." | Clear README, clean reports, documented design |

## 4. Core User Journeys

### Journey A: First scan through the API
1. The user starts the service, optionally sets the two provider keys, and opens the interactive API documentation (later, the ToolGuard-fe new scan screen).
2. They submit a sample manifest or a server URL and receive a scan ID immediately.
3. Polling the scan shows its status and current stage (ingest, rules, triage, AI review, scoring, report).
4. When complete, the scan summary shows the number of tools scanned, findings by severity, the verdict, the top findings, and whether AI review ran.
5. They browse findings with filters, or fetch the Markdown report for details.

Success: the user understands the top finding and what to do about it without reading documentation.

### Journey B: CI gate through the API
1. A pipeline step submits a scan with a failure threshold such as fail_on high, then polls until it completes.
2. The step fails the job when the verdict is fail, and prints the summary's counts and top findings in compact form, plus the scan ID for the full report.
3. A pipeline that requires full AI review can also fail when the scan is degraded.

Success: a failing build tells the developer which tool and which text caused it, in the log itself.

### Journey C: Investigating a finding
1. The user opens a finding in the report.
2. They see: severity, category, tool name, the matched text with surrounding context, a plain explanation of the risk, which stage found it (rule or AI judge), and a suggested next step.
3. If the finding came from AI review, it is labeled as such along with a confidence indication.

Success: the user can decide quickly whether it is a real problem or a false positive.

### Journey D: Rescan and compare (later version)
1. The user rescans a server they already approved.
2. The report highlights tools that are new, removed, or changed, and re-evaluates only the changed ones.

Success: a silent change in a trusted server's tool descriptions is impossible to miss.

### Journey E: Running with exhausted AI quota
1. The user scans while free quota is used up.
2. The scan completes anyway. The report carries a clear banner that AI review was skipped and lists which tools would have been reviewed.
3. The user can rerun later for the AI portion.

Success: no failure, no confusion, and no false sense of full coverage.

## 5. Frontend-Ready API Experience

The API is the product's only interface, so its shape is the product experience for both the frontend and automation. Exact contracts are in sdd.md section 18.

### 5.1 Resources
| Resource | Purpose |
|---|---|
| scans | Submit a scan, list scan history, see a scan's progress and summary |
| findings | Browse a scan's findings |
| tools | Browse every tool in a scan, including tools with no findings |
| report | Download the full report in JSON or Markdown |
| rules | List available rules with IDs, categories, and descriptions |
| budget | Remaining AI quota per provider and reset times |
| health | Service liveness |

### 5.2 Progress
- Submitting a scan returns immediately with an identifier and a status.
- The scan shows its status and the current stage, so a progress view can be calm and stage by stage rather than a spinner.
- Polling comes first. Streaming progress may be added later without changing what clients already read.

### 5.3 Lists
- Findings, tools, and scan history are paginated.
- Findings can be filtered by severity and category (and by source and tool), and sorted. The default order is most severe first, then tool name.

### 5.4 Scan summary (what a person or pipeline sees first)
The summary follows this order: target and scan time, tool count, the verdict with the threshold used, findings grouped by severity with counts, the top findings in short form (tool, rule ID, one-sentence reason), and the AI review status with its reason.

## 6. API Developer Experience

- Predictable resource names: scans, findings, tools, report, rules, budget, health.
- Errors always use one consistent shape: a stable machine code, a human message, and optional details. Codes never change meaning.
- Reports are available in JSON and Markdown from the same endpoint through a format choice.
- The API is versioned in the path from the first release, and changes within a version are additive only.
- Browser access from the frontend is enabled through a CORS setting, off by default.
- An optional service access token protects a deployed instance. There are no user accounts yet.
- Interactive API documentation is provided automatically by the framework and is part of the product experience.

## 7. Report Design

### 7.1 Structure (in order)
1. Header: target, scan time, tool count, ToolGuard version, rule set version.
2. Verdict block: pass, review, or fail, with the rule used to reach it.
3. AI review status: performed, partial, skipped, or disabled, with the reason.
4. Summary table: findings by severity and by category.
5. Findings, ordered by severity, then tool.
6. Per-tool appendix: every tool scanned, including those with no findings.
7. Method note: what ToolGuard checks and what it does not.

### 7.2 Finding card contents
- Severity label and category.
- Tool name.
- Rule ID and source stage.
- Evidence: the matched text, shortened around the match, with any invisible or unusual characters made visible in a safe way.
- Explanation: one or two sentences on the risk, written for a developer, not a security specialist.
- Suggested action: a concrete next step (remove the instruction, restrict the parameter, review the server's publisher).
- For AI-sourced findings: a short note that the finding came from AI review, plus confidence.
- For likely false positives annotated by the AI judge: a visible review note that explains the reasoning, while the original finding remains shown.

### 7.3 Severity taxonomy

| Level | Meaning | Typical examples |
|---|---|---|
| Critical | Strong evidence of malicious intent or direct data theft | Instruction to send secrets to an external address |
| High | Likely to manipulate the agent or expose sensitive data | Hidden instructions directed at the model, override phrasing |
| Medium | Risky pattern that needs a human look | Unconstrained command or path parameter, cross-tool directives |
| Low | Weak signal or hygiene issue | Missing parameter descriptions, unusually long text |
| Info | Observation with no direct risk | Notable size or structure facts |

### 7.4 Verdict wording
- Pass: "No issues at or above the chosen threshold were detected by ToolGuard's checks. This is not a guarantee of safety."
- Review: "Some findings need human review before this server is trusted."
- Fail: "Findings at or above the chosen threshold were detected."

### 7.5 Degraded-mode wording
A visible banner at the top of the report: AI review was skipped or partial, the reason (for example daily free quota reached), and the number of tools that were not reviewed. The verdict block repeats a short form of this note so it cannot be missed in summaries.

Disabled AI review is not degraded, because nothing failed: AI review was turned off for the scan or no provider keys are configured. The report shows a neutral notice in place of the banner, for example: "AI review was not used for this scan (turned off for this scan). Findings come from deterministic rules only." or "(no AI provider keys are configured)". The verdict block repeats a short form, and the API scan summary carries the disabled status and its reason, so a rules-only result is never mistaken for full coverage.

### 7.6 Safe presentation
Scanned text is untrusted. In every format, it is shown inside clearly delimited evidence blocks, with control and invisible characters made visible and markup neutralized, so that viewing a report cannot trigger the very attack being reported.

## 8. Terminology

| Term | Definition |
|---|---|
| Tool | A callable capability an MCP server exposes, with a name, description, and input schema |
| Finding | A specific risk signal tied to a tool and to evidence text |
| Rule | A deterministic check that produces findings |
| Triage | The deterministic step that decides which tools receive AI review and in what order, using rule findings and suspicion signals |
| Suspicion signal | A weak deterministic hint, such as markup-like or directive-like text or a risky parameter, that sends a tool to AI review without creating a finding |
| AI review | Evaluation of flagged tools by a language model judge |
| Degraded scan | A scan where AI review was wanted but was skipped or only partial, for example because quota ran out |
| Disabled AI review | A scan that ran rules-only by choice or configuration; not degraded |
| Verdict | The overall outcome of a scan: pass, review, or fail |
| Fixture | A sample tool manifest with expected findings used for regression testing |

## 9. Messaging Guidelines

- Say what happened, why, and what to do next, in that order.
- Prefer concrete wording over generic wording ("The parameter named command accepts any text" rather than "Insecure schema").
- Avoid fear language. Describe the risk, not the danger.
- Never blame the user. Input errors say what was expected.
- Never display secrets, keys, or full scanned payloads in error messages.

Examples of message intent (not final copy):
- Bad input: states which field was invalid and the accepted format.
- Unsafe target: states that the address points to a private or internal network and was not contacted.
- Provider problem: states that the AI provider was unavailable, that the next option was tried, and the final status.
- Quota reached: states which provider's limit was reached and when capacity is expected to be available again (Groq quota refills continuously; OpenRouter's free daily limit is assumed to reset with the UTC day).

## 10. Onboarding Experience

The README is part of the product. Its order:
1. One paragraph on what ToolGuard does and does not do.
2. A five-minute quickstart: install, start the service, optionally set two keys, and scan the included sample manifest through the interactive API documentation or a single HTTP request.
3. A sample report, so a reader sees the result before installing.
4. How to read a finding.
5. How to use it in CI through the API.
6. How to add a rule and a fixture.
7. Known limits, including free quota behavior and the no-guarantee statement.

The project ships sample manifests: one clean, one with hidden instructions, one with weak schemas, so the first run always shows something meaningful.

## 11. Frontend (ToolGuard-fe, Separate Project)

The frontend is built in a separate repository and attaches to this backend's API. The API must support these screens without backend rework:

| Screen | Purpose | API support |
|---|---|---|
| New scan | Paste a manifest or enter a server URL, choose a failure threshold | Submit scan |
| Scan progress | Stage-by-stage status, live and calm | Scan status and current stage (polling, streaming later) |
| Report | Verdict, severity summary, filterable findings list, evidence viewer | Scan summary, findings and tools lists, report download |
| History | Past scans per server, with changes highlighted in a later version | Scan history list |
| Budget | Remaining AI quota and reset times | Budget |

Design notes: severity is never conveyed by color alone, evidence blocks are monospaced and clearly bounded and rendered as text, never as markup, and the degraded banner or disabled notice is persistent on the report screen.

## 12. Accessibility and Inclusivity

- API responses carry every meaning as text (severity words, status words, reason codes with messages), so any client can present them accessibly without relying on color or symbols.
- Markdown reports use proper heading levels and tables with header rows.
- Severity is always written out as a word.
- Plain, direct language suited to non-native English readers.

## 13. Feedback and Learning Loop

- Each finding has a stable ID so it can be referenced in issues and fixtures.
- Users can mark a finding as a false positive locally, which later informs rule tuning. Storage of such marks is local to the user's installation.
- The fixture library grows from real false positives and misses.
- Local counters (scans run, AI calls used, cache hits) help the author understand usage without sending data anywhere.

## 14. Design Risks

| Risk | Effect | Response |
|---|---|---|
| Overclaiming safety | Users trust a clean report too much | Fixed disclaimer wording, method note in every report |
| Too many weak findings | Users ignore the tool | Conservative default threshold, clear Low and Info separation |
| AI findings look more certain than they are | Misplaced trust | Label source and confidence on every AI finding |
| Report viewing becomes an attack path | Security failure | Safe presentation rules in section 7.6 |
| Degraded scans go unnoticed | False sense of coverage | Banner plus repeat in verdict block and API scan summary |

## 15. Open Design Questions

1. Closed: the default failure threshold is high. Fail when any finding is at or above the threshold, review when the highest finding is below it but at least medium, pass otherwise (sdd.md section 14).
2. Closed: no quiet mode is needed. CI clients filter findings through the API (for example min_severity).
3. How should suggested actions be phrased for findings sourced from AI review where the exact fix is uncertain?
4. Should false-positive marks be stored per server, per rule, or both?
5. Is a short badge or summary line for README embedding worth designing for later?