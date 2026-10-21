# ToolGuard: Product Requirements Document

Status: Draft v0.1
Repository: ToolGuard-be (backend). The frontend is a separate repository, ToolGuard-fe, that attaches to this backend's HTTP API.
Owner: Swapnil Bhattacharya

## 1. Overview

ToolGuard is a security scanner and regression test harness for MCP (Model Context Protocol) servers. It inspects the tools an MCP server exposes (names, descriptions, input schemas, annotations), finds risky or malicious patterns, and produces a clear report that a developer or a CI pipeline can act on.

ToolGuard is a product with a backend and a frontend. This repository is the backend. Its versioned HTTP API is the only interface of the product: the frontend, CI jobs, and scripts are all clients of the same API.

The core idea is a layered pipeline. Cheap deterministic rules run first, a deterministic triage step decides which tools still need a closer look, and a language model judges only the items that remain suspicious or ambiguous. This keeps the product accurate, explainable, and usable on free model quotas.

## 2. Problem

Teams are connecting third-party MCP servers to AI agents quickly, and tool descriptions are text that the agent's model reads and trusts. That makes them a place where attacks can hide: instructions embedded in a description, tools that try to exfiltrate data, hidden characters, overly broad permissions, and tools that impersonate or shadow other tools. Tool-level visibility has also become a security expectation after public disclosures in 2026.

Today most teams have no repeatable way to check an MCP server before connecting it to an agent, and no way to notice when a server's tools change after they approved it.

## 3. Goals and Non-Goals

### Goals
- Build a working, well-structured scanner that is a strong AI engineering portfolio project.
- Detect common MCP tool risks with explainable findings (every finding says why and shows evidence).
- Work entirely on free model quotas, degrading gracefully when quota runs out.
- Provide a regression harness so a scan can be repeated and compared over time and used as a CI gate.
- Keep the backend simple: a FastAPI service and a small set of clearly separated modules.
- Expose a stable, documented API that a frontend can attach to without backend rework.

### Non-Goals (for now)
- Commercial features such as billing, teams, or user accounts. The project is built first and monetization is considered later.
- Running or executing untrusted MCP servers on the ToolGuard host (see Security Principles).
- A frontend in this repository. The frontend is a separate project (ToolGuard-fe) that consumes this API.
- A command line interface. The HTTP API is the only interface.
- Real user accounts. An optional shared service access token protects the API for now.
- Guaranteeing that a clean report means a server is safe. ToolGuard reports risk signals, not proof of safety.
- Local model hosting (Ollama), since the service is meant to be deployed.

## 4. Target Users

- Primary: AI and backend engineers who add third-party MCP servers to agents and want a quick check before trusting them.
- Secondary: platform and security engineers who want a release gate in CI for internal MCP servers.
- Tertiary (portfolio audience): reviewers evaluating the project's engineering quality.

## 5. User Stories

1. As a developer, I submit an MCP server's tool list and receive a report of findings ranked by severity.
2. As a developer, I can see exactly which part of a tool description triggered each finding and why it matters.
3. As a developer, I rescan the same server later and see what changed since the last scan.
4. As a CI owner, I submit a scan through the API with a fail_on threshold and use the returned verdict and counts to pass or fail my pipeline.
5. As a maintainer of an MCP server, I keep a set of known-good and known-bad fixtures so I can test my own detection rules.
6. As an operator, I can see how much of the free AI quota is left and I am told when a report was produced without AI review.
7. As a frontend developer, I build the ToolGuard interface against a stable, documented API with progress, history, pagination, filtering, and consistent errors.

## 6. Scope

### MVP (v0.1)
- Input: an uploaded or pasted tool manifest (JSON list of tools), and a remote MCP server URL over HTTP.
- Deterministic rule engine with an initial rule set (section 7).
- Deterministic triage that selects and orders tools for AI review (FR3). A model-based prompt-injection classifier was evaluated in M0 and is not part of the MVP.
- LLM judge for flagged or ambiguous tools, with structured output, using Groq as primary and OpenRouter as fallback.
- Result caching by content hash, and a daily AI-call budget guard.
- Reports in JSON and Markdown.
- FastAPI service with a versioned, frontend-ready HTTP API (section 7, FR8).
- Rules-only scans work with no provider keys configured; AI review is then reported as disabled.
- SQLite storage for scans, findings, and cache.

### v0.2 and later
- Change detection between scans of the same server (a description that changed after approval).
- Local stdio server scanning in an isolated sandbox.
- A GitHub Action that calls the API.
- Streaming scan progress as an addition to polling.
- Evaluation of judge quality using a labeled dataset.
- Optional observability dashboard.
- Postgres storage and multi-user support.

## 7. Functional Requirements

### FR1: Ingestion
- Accept a tool manifest as JSON and normalize it into an internal tool model (server name, tool name, description, input schema, annotations).
- Accept a remote MCP server URL, connect over HTTP, list tools, and normalize them the same way.
- Reject oversized inputs and block requests to private or internal network addresses.

### FR2: Deterministic rules
Initial rule categories:
- Hidden or instruction-like text in descriptions (directives aimed at the model, "ignore previous" style phrasing, instructions to act before or instead of the user's request).
- Hidden characters (zero-width characters, bidirectional controls, look-alike characters).
- Data exfiltration signals (instructions to send data to external URLs, requests to read secrets, keys, or environment values).
- Cross-tool manipulation (a description that tells the model how to use or override other tools).
- Excessive permissions in schemas (free-form command, path, or query parameters with no constraints; missing descriptions or types).
- Name risks (tool names that collide with or imitate other tools).
- Abnormal size or structure (very long descriptions, unusual encodings).

Every rule has an ID, severity, category, explanation, and a link between the finding and the exact text span that matched.

### FR3: Triage (deterministic, no model)
- Decide which tools need AI review from rule findings and deterministic suspicion signals: risky capability hints, markup-like or directive-like text, and unusually long descriptions.
- Skip tools whose rule findings already reach the fail threshold, since AI review cannot change the verdict, and skip clean tools.
- Order the remaining tools by suspicion so the most suspicious are reviewed first within the per-scan cap; list the rest as unreviewed.
- A model-based prompt-injection classifier (Llama Prompt Guard 2 on Groq) was evaluated in M0 and removed: it detected classic jailbreak phrasing but not realistic tool-poisoning descriptions, it is a preview model, and it accepts at most 512 tokens.

### FR4: LLM judge
- Only tools selected by triage go to the judge.
- The judge returns validated structured findings, never free text that the system has to interpret.
- The judge may add findings, raise severity, and explain. It may never delete or downgrade a deterministic high or critical finding; it can only annotate it as a likely false positive for human review.
- Several tools may be sent together in one call to save quota.
- The judge uses structured tool output with severity restricted to the defined levels, and its prompt defines each level.

### FR5: Quota and degradation
- Track AI calls and tokens per provider and stop before limits are exceeded. Groq usage follows its rate limit response headers; OpenRouter usage is counted by ToolGuard.
- Move to the next model on provider errors, rate limits, and invalid output, without hidden client retries.
- When AI review is unavailable, still return the rule-based results with a clear "AI review skipped" flag.

### FR6: Caching
- Cache judge results by a hash of the normalized tool content plus the prompt version, so rescanning the same tool costs no AI calls.

### FR7: Reports
- Produce a JSON report (machine readable) and a Markdown report (human readable) containing: target, scan time, tool count, findings grouped by severity, per-finding evidence and explanation, overall verdict, AI usage summary, and a degraded flag when relevant.

### FR8: HTTP API (the only interface)
- Versioned REST under /v1 with a stable, documented OpenAPI schema.
- Endpoints for: submitting a scan, scan history listing, scan detail with progress, findings, per-tool results, the report in JSON and Markdown, remaining AI budget, and service health.
- Scan progress through polling first (status plus current stage), designed so streaming progress can be added later without breaking clients.
- Findings and other lists support pagination, filtering by severity and category, and sorting.
- One consistent error shape with stable machine-readable error codes.
- Configurable CORS through a setting, off by default.
- An optional service access token. Real user accounts are out of scope.
- CI use is served by the API: a scan request accepts a fail_on threshold, and the scan response carries the verdict, counts, and degraded flag.

### FR9: Regression harness
- A fixture library of benign and malicious tool manifests with expected findings.
- A pytest suite that runs the full pipeline against fixtures, reports mismatches, and reports detection and false positive rates.
- Fixtures run with a fake model by default so tests never use real quota.

## 8. Non-Functional Requirements

- Free-tier friendly: average of three or fewer AI calls per scan, and a hard daily budget guard.
- Resilience: a scan never fails only because a model provider is down or out of quota.
- Latency: a typical manifest scan completes in under 30 seconds at the 95th percentile when AI review is used, and in a few seconds without it.
- Explainability: every finding has evidence, a rule or source, and a human-readable reason.
- Maintainability: clear module boundaries, typed models, and tests for each rule.
- Security of the scanner itself: see section 9.
- Configuration: secrets and settings come from environment variables only.
- API stability: changes within /v1 are additive only, so the frontend does not break when the backend evolves.

## 9. Security Principles

- Scanned content is untrusted data. It is never executed and never treated as instructions, including when it is shown to the judge model.
- The MVP does not run stdio-based MCP servers. Executing untrusted code on the host would make the scanner a target.
- The judge agent has no tools, receives data inside clearly marked blocks, and can only return a fixed structured shape.
- URL fetching blocks private and internal addresses, limits redirects, size, and time.
- Reports escape or neutralize scanned text so a malicious description cannot attack whoever views the report.
- API keys are read from the environment, never logged, and never included in reports.

## 10. Constraints and Assumptions

- No paid model API access. Free tiers only, and OpenRouter credits are not being added for now.
- Primary provider is Groq (free tier) using an OpenAI GPT-OSS model for judging; OpenRouter free models are the fallback. Limits as read from provider documentation in October 2026 are roughly: Groq 30 requests per minute, 1,000 requests per day, 8K tokens per minute, 200K tokens per day for the GPT-OSS models; OpenRouter free models 20 requests per minute and 50 requests per day without purchased credits. These figures change and must be re-verified. M0 observed that Groq's daily request quota refills continuously (one request every 86.4 seconds) rather than resetting at a fixed time.
- Limits apply per organization or account, so extra keys do not add capacity.
- Free model availability can change without notice, so model names are configuration, not hard-coded.
- Prompts are sent to third-party providers, so real customer data should not be used during development.
- Framework: Pydantic AI.

## 11. Success Metrics (initial targets, to be tuned)

- Detection quality on the project's own fixture corpus: at least 90 percent of seeded malicious fixtures flagged, and under 10 percent of benign fixtures falsely flagged.
- AI efficiency: average of three or fewer AI calls per scan, with cache hits on repeated scans.
- Reliability: zero failed scans caused only by quota exhaustion or provider errors.
- Developer experience: a new user can start the service and scan a sample manifest through the API in under five minutes following the README.
- Portfolio value: documented architecture, tests, and a short write-up of design decisions.

## 12. Milestones

These match instruction.md section 8 and sdd.md section 24.

1. M0 (done): Verification spikes and project setup: uv project, settings from environment, empty package structure, provider spikes. Findings in spikes/FINDINGS.md.
2. M1 (done): Domain models, manifest ingestion with content hashing, and the first fixture corpus.
3. M2: Deterministic rule engine with evidence positions and tests for each rule.
4. M3: SQLite storage and the scan pipeline without AI, including JSON and Markdown reports.
5. M4: Model layer with Groq primary and OpenRouter fallback models, rate limiting, header-driven budget ledger, cache, and fake-model tests.
6. M5: Deterministic triage and the LLM judge with its model loop, authority rules, degraded scan handling, the unreviewed tools list, and token estimate calibration.
7. M6: Frontend-ready HTTP API and the pytest fixture regression harness with detection and false positive rates.
8. M7: Remote MCP server ingestion over HTTP, hardening, README, deployment, and design write-up.

## 13. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Free quota too small | Reduced AI review | Rules and deterministic triage first, per-scan review cap, batching, caching, budget guard, graceful degradation |
| Free models withdrawn or renamed | Judge stops working | Model names in config, an ordered model list with fallback models, tests that do not depend on live models |
| Weak model produces malformed or unreliable verdicts | Bad findings | Strict structured output, validation retries, LLM cannot erase deterministic findings |
| Judge is manipulated by malicious descriptions | Missed attacks | Data-only framing, no tools, constrained output, deterministic rules as ground truth |
| False positives annoy users | Low trust | Evidence-based findings, severity levels, tunable thresholds, fixture-based tuning |
| Third-party data policies on free tiers | Privacy concern | No real customer data during development, document data flow, review provider terms before any commercial step |

## 14. Open Questions

Answered in M0 (spikes/FINDINGS.md): structured output works in all modes and tool output is used; the classifier was evaluated and removed; Groq's quota refills continuously and is tracked from response headers.

Still open:
1. Is OpenRouter's free daily counter shared across all free models, and when does it reset?
2. Should observability use a hosted service free tier or just local structured logs for now?
3. Which hosting platform will be used for deployment, given the single-instance limitation of SQLite and in-process counters?
4. Which MCP transport and authentication cases must the HTTP ingestion support in v0.1?