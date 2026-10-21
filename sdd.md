# ToolGuard: Software Design Document (SDD)

Status: Draft v0.1
Companion documents: prd.md, architecture.md, pdd.md

This document specifies the detailed design of the ToolGuard backend: module contracts, data schemas, algorithms, interfaces, error handling, and configuration. It contains no source code. Interfaces are described as contracts (inputs, outputs, behavior) so that implementation choices stay open until coding begins.

## 1. Scope and Conventions

- Language and runtime: Python, with the minimum version taken from Pydantic AI's installation requirements (to be confirmed at setup).
- Web framework: FastAPI. Agent and model framework: Pydantic AI. MCP connectivity: the official MCP Python SDK.
- Typed models: all data crossing module boundaries is a typed Pydantic model. No untyped dictionaries between stages.
- Time values are stored in UTC. Quota windows follow each provider's observed behavior (section 10): a refilling bucket for Groq, the UTC day for OpenRouter.
- Versioned artifacts: report schema version, rule set version, and judge prompt version are included in every report and cache key.

## 2. Module Overview

| Module | Responsibility | Depends on |
|---|---|---|
| config | Load and validate settings from the environment | none |
| core.models | Typed domain models shared by all modules | none |
| ingest | Turn a manifest or a server URL into normalized tools | core.models, config |
| rules | Deterministic detection rules and registry | core.models |
| llm.provider | Build the ordered model list and per-model settings, rate limiting, read provider quota headers | config, llm.budget |
| llm.budget | Track and enforce daily AI usage | storage |
| llm.cache | Store and fetch judge results | storage |
| llm.judge | Judge agent, batching, output validation, the model loop over the ordered model list | llm.provider, llm.budget, core.models |
| core.triage | Decide which tools need AI review | core.models |
| core.pipeline | Orchestrate stages for one scan | all of the above |
| core.scoring | Aggregate findings, compute verdict | core.models |
| reporting | Render JSON and Markdown reports | core.models |
| storage | SQLite access | config |
| api | HTTP routes, request and response schemas, error mapping, CORS, access token | core.pipeline, reporting, storage, config |

Dependency direction: api depends on core; core depends on rules, ingest, llm, reporting, and storage through narrow interfaces; nothing depends on api. The HTTP API is the only interface of the product.

## 3. Configuration (config module)

Settings are read from environment variables and a local .env file using pydantic-settings. The loader validates them at startup and fails fast with a clear message if a value is invalid. Names in the existing .env (groq_api_key and openrouter_api_key) are kept. pydantic-settings matches names case-insensitively by default, so the lowercase names work (confirmed in M0 and covered by a test).

Provider keys are optional. The service never refuses to start because a key is missing. With neither key set, every scan runs rules-only and reports ai_review_status as disabled with reason no_provider_keys. With only one key set, the model list contains only that provider's models.

| Setting | Purpose | Default |
|---|---|---|
| groq_api_key | Groq access | optional, unset |
| openrouter_api_key | OpenRouter access | optional, unset |
| primary_model | Groq model identifier for judging | configured, not hard-coded |
| fallback_models | Ordered OpenRouter model identifiers | configured list |
| daily_budget_groq_requests, daily_budget_groq_tokens | Safety budgets below provider limits (section 10) | about 90 percent of provider limits |
| daily_budget_openrouter_requests | Safety budget for the shared free-model daily counter | set below 50 |
| rpm_groq, rpm_openrouter | Per-minute request caps | 30 and 20, adjustable |
| judge_max_tools_per_call | Batch size cap | small integer, tuned by token budget |
| judge_max_tools_per_scan | Per-scan cap on tools sent to AI review (section 8) | small integer, tuned by quota |
| judge_output_tokens_per_tool | Output allowance per tool in a batch, including reasoning (section 11.5) | about 250, calibrated in M5 |
| judge_reasoning_effort | Reasoning effort requested from models that support it | low |
| default_fail_on | Fail threshold used when a request does not give one | high |
| judge_max_input_tokens | Estimated input token cap per call | tuned to per-minute token limit |
| ingest_max_bytes, ingest_timeout_seconds, ingest_max_redirects | Ingestion limits | conservative values |
| description_length_threshold | Rule threshold for long descriptions | tuned on fixtures |
| database_path | SQLite file location | local file |
| service_access_token | Optional shared secret for the API | unset in local use |
| cors_allowed_origins | Explicit list of origins allowed to call the API from a browser | empty, CORS off |
| api_max_page_size | Upper bound for page_size on list endpoints | 200 |
| log_level | Logging verbosity | info |

## 4. Domain Models (core.models)

### 4.1 Enumerations
- Severity: info, low, medium, high, critical (ordered).
- Category: injection, exfiltration, hidden_characters, cross_tool, excessive_permissions, naming, structure.
- FindingSource: rule, judge.
- ScanStatus: pending, running, completed, failed.
- ScanStage: queued, ingest, rules, triage, ai_review, scoring, reporting, done. Exposed to clients as progress.
- Verdict: pass, review, fail.
- AIReviewStatus: performed, partial, skipped, disabled. Skipped and partial mean AI review was wanted but did not fully happen (the scan is degraded). Disabled means AI review was not requested or not possible by configuration (the scan is not degraded).
- AIReviewReason: stable codes explaining the status: disabled_by_request, no_provider_keys, quota_exhausted, provider_unavailable, judge_output_invalid, scan_timeout, review_cap_reached, no_tools_needed_review.

### 4.2 Tool
| Field | Description |
|---|---|
| server_name | Name reported by the server or supplied by the user |
| name | Tool name |
| description | Description text, possibly empty |
| input_schema | JSON schema describing parameters |
| annotations | Optional hints such as read-only or destructive markers |
| content_hash | Hash of the normalized name, description, schema, and annotations |

Normalization rules: trim, normalize Unicode to a single form for hashing only, never alter the original text used as evidence, and sort schema keys for stable hashing.

### 4.3 Evidence
| Field | Description |
|---|---|
| field | Which part of the tool: name, description, or a schema path |
| text | The matched excerpt |
| start, end | Character positions within the field |
| context | Short surrounding text |

### 4.4 Finding
| Field | Description |
|---|---|
| id | Stable identifier within the scan |
| source | rule or judge |
| rule_id | For example TG-INJ-001, or a judge category code for AI findings |
| category | One of the categories above |
| severity | Severity enum |
| tool_name | Tool the finding applies to |
| evidence | One or more Evidence items |
| explanation | Short human-readable reason |
| suggested_action | Short next step |
| confidence | Number from zero to one, for judge findings |
| review_note | Optional note, for example judge annotation of a likely false positive |

### 4.5 JudgeVerdict (judge output unit)
| Field | Description |
|---|---|
| tool_name | Tool judged |
| risk_level | Judge's overall severity estimate, restricted to the Severity values (validation rejects anything else) |
| added_findings | New findings the judge identifies |
| annotations | Notes on existing deterministic findings, including likely false positive markers |
| reasoning_summary | Short explanation, bounded in length |

### 4.6 ScanReport
| Field | Description |
|---|---|
| scan_id, target, started_at, finished_at | Identification and timing |
| status, verdict | Outcome |
| fail_on | Fail threshold used for the verdict |
| tool_count | Number of tools scanned |
| counts | Finding counts by severity and by category |
| findings | All findings after aggregation |
| tool_results | Per-tool summary including tools with no findings |
| ai_review_status | performed, partial, skipped, or disabled, with a reason code and a short message |
| degraded, degraded_reason | True when AI review was skipped or partial; the reason code |
| unreviewed_tools | Tools that qualified for AI review but did not receive it |
| ai_usage | Calls and estimated tokens per provider, cache hits |
| versions | ToolGuard, rule set, prompt, and report schema versions |

## 5. Ingestion Design (ingest)

### 5.1 Manifest ingestion
Input: a JSON body or file containing a list of tools, optionally with a server name. Behavior: validate shape, enforce size limits, normalize into Tool models, compute hashes. Invalid entries produce a clear error naming the entry index and field. Partial acceptance is not allowed: if any entry is invalid, ingestion fails with a list of all problems.

### 5.2 Remote ingestion (HTTP)
Input: a server URL. Behavior: validate the URL, resolve the host, reject private, loopback, link-local, and otherwise internal addresses (checked after resolution and again on every redirect), connect with the MCP SDK over HTTP, request the tool list only, and normalize results. The connection never invokes any tool. Limits: total bytes, total time, redirect count. Failures map to the ingestion error types in section 13.

Authentication: the first version supports unauthenticated servers and an optional bearer token supplied per scan and never stored or logged. Other schemes are deferred.

### 5.3 Deferred
Local stdio servers are not launched in the first version.

## 6. Rule Engine Design (rules)

### 6.1 Rule contract
A rule has: an ID, a category, a default severity, a short description, and an evaluation behavior that takes a Tool and returns zero or more Findings. Rules are pure: no network, no model, no shared mutable state. A registry lists rules and supports enable, disable, and severity override through configuration.

### 6.2 Initial rule catalog

| Rule ID | Category | Default severity | Detects |
|---|---|---|---|
| TG-INJ-001 | injection | high | Directive text aimed at the model within a description |
| TG-INJ-002 | injection | high | Phrasing that tries to override or disregard prior instructions |
| TG-INJ-003 | injection | high | Instructions to hide actions or information from the user |
| TG-HID-001 | hidden_characters | high | Zero-width or invisible characters in names or descriptions |
| TG-HID-002 | hidden_characters | high | Bidirectional control characters |
| TG-HID-003 | hidden_characters | medium | Mixed-script or look-alike characters in tool names |
| TG-EXF-001 | exfiltration | critical | Instruction to send data to an external address |
| TG-EXF-002 | exfiltration | high | References to secrets, keys, credentials, or sensitive files in directives |
| TG-EXF-003 | exfiltration | medium | Large encoded blobs in descriptions |
| TG-XTL-001 | cross_tool | medium | Description that directs how other tools must be used |
| TG-XTL-002 | cross_tool | medium | Instruction to call another tool before or after this one |
| TG-SCH-001 | excessive_permissions | medium | Unconstrained free-form parameters such as command, path, query, url, or code |
| TG-SCH-002 | excessive_permissions | low | Missing type or description on parameters |
| TG-SCH-003 | excessive_permissions | low | Schema accepts arbitrary additional properties |
| TG-SCH-004 | excessive_permissions | medium | Destructive-sounding tool without a destructive or read-only hint |
| TG-NAM-001 | naming | medium | Duplicate or colliding tool names within a scan |
| TG-NAM-002 | naming | medium | Names that closely imitate common tool names |
| TG-SIZ-001 | structure | low | Description longer than the configured threshold |
| TG-SIZ-002 | structure | info | Unusual schema depth or structure |

The catalog is an initial proposal. Thresholds and patterns are tuned against the fixture corpus. Each rule ships with at least one positive fixture and one negative fixture.

### 6.3 Evidence capture
Rules must record exact positions and excerpts so reports can show the match. Invisible characters are recorded by code point name, not rendered raw.

### 6.4 Rule performance
All rules together should process a typical manifest in well under a second. No rule may use unbounded backtracking patterns; pattern complexity is reviewed.

## 7. Classifier Stage (removed from the MVP)

A prompt-injection classifier stage (Groq-hosted Llama Prompt Guard 2) was evaluated in the M0 spikes and removed. It scored a classic injection phrase at 0.9996 but two realistic tool-poisoning descriptions at only 0.0023 and 0.0027, close to a benign description (0.0004). It recognizes jailbreak phrasing, not instructions written as tool documentation, which is what ToolGuard scans. It is also a preview model that may be discontinued at short notice, and it rejects input over 512 tokens. Details are in spikes/FINDINGS.md. Triage relies on rule findings and the deterministic suspicion signals in section 8. Section number 7 is kept so other references stay valid.

## 8. Triage Design (core.triage)

Triage is a pure function with no model calls.

Inputs: per-tool rule findings, the scan's fail_on threshold, and the suspicion signals below, all computed deterministically from the tool.
Output: for each tool, skip or review, plus a suspicion order used when the per-scan cap applies.

Suspicion signals (weak signals that do not create findings on their own):
- Risky capability hints: parameters that look like free-form execution, file paths, URLs, queries, or code, and destructive-sounding tool names.
- Markup-like text in the description: tags such as \<IMPORTANT\>, HTML comments, code fences, or unusual bracketed blocks.
- Directive-like text in the description: imperative wording aimed at the model, such as always, never, must, before using, do not tell, below the thresholds the rules use.
- Description length above description_length_threshold.

Interpretation principle: where the policy can be read two ways, the reading that sends more tools to review is used, since a missed risk costs more than an extra AI call. The per-scan cap still applies.

Decision policy (the first matching rule wins):
1. Skip when the tool has a rule finding that already reaches the scan's fail_on threshold, since AI review cannot change the verdict. The tool is still reported as fail-worthy. A high or critical finding below fail_on (for example fail_on is critical) does not skip.
2. Review when the tool has any rule finding.
3. Review when the tool has at least one suspicion signal.
4. Skip otherwise: no rule findings and no suspicion signals.

Suspicion order: tools selected for review are sorted by highest rule finding severity, then the number of suspicion signals, then description length, all descending, with tool name as a stable tie-breaker. When the cap or the budget is hit, the first tools in this order are reviewed and the remainder are listed in unreviewed_tools. When AI review is disabled, triage does not run and unreviewed_tools is empty.

Quota impact: without a classifier, no tool is skipped on the basis of a low score, so more tools reach the judge. Three controls keep this within free quota:
- The per-scan cap (judge_max_tools_per_scan, default 10) with batching (judge_max_tools_per_call, default 5) bounds a fresh scan to about 2 judge calls, within the PRD target of 3 or fewer.
- The cache (section 12) means an unchanged tool is never judged twice for the same prompt version, so rescans cost close to zero calls.
- The budget ledger (section 10) stops before limits; the binding Groq limit in practice is tokens per day (200K, with a 180K safety budget). At about 3,500 tokens per full batch (section 10.2), that is roughly 50 full batches, or about 25 fresh capped scans per day, before falling back to OpenRouter or degrading. These figures are recalculated after token calibration in M5.

## 9. Model Layer (llm.provider)

### 9.1 Responsibilities
Build the ordered model list and per-model settings, enforce rate limits, read provider quota headers into the budget ledger, and translate provider failures into internal results. The model loop itself lives in the judge (section 11.7).

### 9.2 Model list
Order: Groq primary model, then the configured OpenRouter fallback models, skipping any provider without a key. Model identifiers come from configuration. Each model has its own settings: temperature, output limit (section 11.5), and reasoning effort where the model supports it.

Pydantic AI's FallbackModel is not used. The M0 spikes and the framework documentation show that it falls back only on provider API errors: output validation failures raise UnexpectedModelBehavior after the output retries and never reach the next model, and FallbackModel offers no hook to consult the budget ledger before each attempt. A plain loop in the judge covers both cases (section 11.7).

Provider client retries are set to zero (max_retries 0 in the model settings). The Groq and OpenAI clients that Pydantic AI builds otherwise retry twice on their own, which would hide real calls from the ledger and delay moving to the next model.

### 9.3 Rate limiting
A limiter per provider enforces the per-minute request cap (rpm_groq, rpm_openrouter). For Groq, the tokens-per-minute budget comes from the response headers (section 10): before each call the estimated tokens (section 10.2) must fit in the tokens currently available, otherwise the call waits for the bucket to refill or the loop moves on. Concurrency is capped so batches do not overlap beyond the limits.

Quota headers are read by a response event hook on the httpx client passed to each provider, so only the model layer sees provider details.

### 9.4 Failure handling
- 429 or quota error: mark the provider as cooling down until retry-after (or the header-derived refill time), then the judge moves to the next model.
- Authentication error: mark the provider unavailable for the process lifetime and log a clear configuration message without the key.
- Timeout, connection error, or server error: no retry on the same model; the judge moves to the next model.
- All models unavailable or exhausted: the judge returns a quota or provider result that the pipeline turns into a degraded scan.

## 10. Budget Ledger (llm.budget)

### 10.1 Data
- A call log: one row per real provider call with provider, model, time, estimated tokens, actual input and output tokens (output includes reasoning), and outcome. Rolling-window sums come from this log.
- A Groq quota snapshot: the latest x-ratelimit-limit-requests, x-ratelimit-remaining-requests, x-ratelimit-reset-requests, x-ratelimit-limit-tokens, x-ratelimit-remaining-tokens, x-ratelimit-reset-tokens, and the time they were observed.
- A cooldown per provider: the time until which it must not be called (from retry-after or a quota error).
- Optionally the last OpenRouter free_model_daily_requests reading and when it was taken.

### 10.2 Token estimate
estimate = estimated input tokens + the full output limit for the call (judge_output_tokens_per_tool times the tools in the batch). Reasoning counts as output on both providers (M0 spike 04), so the estimate never assumes the answer alone. Input is estimated as about 250 tokens of fixed overhead (instructions and output schema) plus, per tool, description characters divided by 4, the schema excerpt, about 30 tokens per known finding, and about 30 tokens of framing. These figures come from small M0 samples and are calibrated on the fixture corpus in M5.

### 10.3 Groq: refilling bucket driven by headers
The M0 spikes showed that Groq quota refills continuously rather than resetting at a fixed time: after 7 requests the headers reported 993 of 1,000 remaining with a reset of 10m4.8s, which is 7 times 86.4 seconds (86,400 seconds divided by 1,000 requests). Tokens per minute behave the same way (79 tokens used, 592 ms reset at 8,000 per minute). Groq documents that x-ratelimit-*-requests refers to requests per day and x-ratelimit-*-tokens to tokens per minute, but not the refill algorithm, so this is an observed behavior and is re-checked by the live smoke test.

Algorithm before a Groq call:
1. If Groq is cooling down, report it exhausted until the cooldown ends.
2. Requests available now = min(limit, remaining at observation + elapsed seconds times limit / 86,400). Tokens available now = min(limit, remaining at observation + elapsed seconds times limit / 60).
3. Allow the call only if one request leaves at least the reserve implied by daily_budget_groq_requests (by default 10 percent of the limit) and the token estimate fits the tokens available now.
4. Limits the headers do not report are enforced from the call log: requests in the last 60 seconds against rpm_groq, and tokens in the last 24 hours against daily_budget_groq_tokens.
5. If there is no snapshot (first call, or after a restart before any response), use the call log alone: requests in the last 24 hours against daily_budget_groq_requests.
6. After every response, successful or not, replace the snapshot from the headers when present, and record actual usage in the call log. On a 429, set the cooldown from retry-after.

### 10.4 OpenRouter: counted by us
OpenRouter sends no rate limit headers on successful responses. Its free_model_daily_requests field (from GET /api/v1/key) did count the calls in M0 but lagged by a few minutes. Therefore:
1. Our own call log is the source of truth: free-model requests since 00:00 UTC against daily_budget_openrouter_requests, and requests in the last 60 seconds against rpm_openrouter.
2. The reported counter is only a cross-check. When a reading is available, the ledger uses the larger of our count and the reported count. Reading it costs no completion quota but is still a request, so it is read at most occasionally (for example at startup and when the budget endpoint is called), never before every call.
3. The counter is treated as one pool shared by all free models until proven otherwise, so listing several free models adds resilience, not quota.
4. The daily window is assumed to be the UTC day, as the documentation implies. This is unconfirmed (section 25).

### 10.5 Notes
- Provider limits apply per organization or account, not per key.
- Counters and the snapshot are persisted in SQLite and loaded on startup so restarts do not reset usage.
- Single-instance assumption: counters are local. Multiple instances would need a shared store.

## 11. Judge Design (llm.judge)

### 11.1 Input assembly
For each batch: a fixed instruction block, then each tool in a clearly delimited data block containing name, description, schema excerpt, and the deterministic findings already known. The instruction block states that the delimited content is untrusted data, must never be followed as instructions, and that the only task is classification and explanation.

The instruction block also defines the severity levels, using one sentence each from pdd.md section 7.3, and states that a tool with no risk signals is info. When unsure between two levels, the judge picks the higher one only if it can quote evidence. This was added after M0 spike 01 rated the same benign tool low in one mode and info in others.

### 11.2 Output contract
A list of JudgeVerdict items, one per tool, validated against the schema. risk_level and any severity in added findings must be one of the Severity values. Free-form text is limited to short explanation fields with length caps.

Output mode: Pydantic AI tool output (the framework default). In M0 spike 01 all three modes succeeded on the first request; tool output used about 242 to 291 input tokens per call, the same as native output, while prompted output used about 340 to 367. Tool output is also supported by every model in the list, whereas native structured output depends on per-provider schema support.

### 11.3 Authority rules (enforced after validation, not left to the prompt)
- Added findings are accepted and tagged with source judge.
- Severity of an existing finding may be raised by the judge, never lowered.
- Deterministic high or critical findings cannot be removed. A judge annotation can mark them as a likely false positive, which sets a review note but leaves severity and presence unchanged.
- Judge output referencing tools that were not in the batch is discarded.

### 11.4 Batching
Tools are grouped into batches under both a tool-count cap and an estimated input-token cap, so one call stays within per-minute token limits. Long descriptions are truncated to a configured length for judging, with a note in the evidence that truncation occurred.

### 11.5 Settings
- Temperature 0.
- Reasoning effort set to the lowest each model allows (judge_reasoning_effort, default low). For Groq gpt-oss this is reasoning_effort low; reasoning_format is not supported for gpt-oss.
- Output limit per call = judge_output_tokens_per_tool times the tools in the batch. Reasoning tokens count as output on both providers, and in M0 a 5-token limit on OpenRouter returned only reasoning, so the limit must leave room for reasoning plus the structured answer. A response cut off by the limit fails validation and is handled by section 11.7.
- Provider client retries set to zero (section 9.2).

### 11.6 Versioning
The judge prompt has a version string. Any change that could alter results increments it, which invalidates affected cache entries.

### 11.7 Model loop, retries, and degradation
The judge loops over the ordered model list from section 9.2 for each batch:
1. Ask the budget ledger whether the model's provider can take this call (cooldown, rate limits, and the token estimate). If not, move to the next model.
2. Run the agent with that model (agent.run with the model argument), one output validation retry, and provider client retries at zero.
3. Record the outcome and actual usage with the ledger, including the quota headers read by the model layer. This happens after every attempt, successful or not.
4. On success, return the validated verdicts.
5. On a provider error (ModelAPIError: rate limit, quota, server error, timeout, connection, authentication), apply the failure handling in section 9.4 and move to the next model.
6. On invalid output (UnexpectedModelBehavior after the validation retry, including content filter and incomplete tool call), move to the next model.
7. If no model succeeds, the batch is marked unreviewed and the scan is flagged degraded with reason quota_exhausted, provider_unavailable, or judge_output_invalid, whichever applied to the last attempt.

One attempt therefore costs at most 2 requests on a model (the call and one validation retry), and the ledger counts each.

## 12. Cache Design (llm.cache)

- Key: content hash of the tool, judge prompt version, and a coarse model family tag so that a change of primary model can be configured to invalidate results.
- Value: the JudgeVerdict, creation time, and provider and model name used.
- Lookup happens before batching; hits are removed from the batch.
- Retention: entries expire after a configured period or on prompt version change.
- Cache entries are never shared across different judge prompt versions.

## 13. Error Taxonomy

| Error | Code | Where | Behavior |
|---|---|---|---|
| InvalidInput | invalid_input | ingest, api | HTTP 400 with details when found at submission, including request validation errors. If found during ingestion, the scan is marked failed with this code |
| UnsafeTarget | unsafe_target | ingest, api | Reject without contacting the address. HTTP 400 at submission, or scan marked failed when found on a redirect |
| IngestionFailed | ingestion_failed | ingest | Connection, protocol, size, or timeout problem; scan marked failed with reason |
| Unauthorized | unauthorized | api | HTTP 401 when the service access token is set and missing or wrong |
| NotFound | not_found | api | HTTP 404 for an unknown scan ID |
| ScanNotReady | scan_not_ready | api | HTTP 409 when a report is requested before the scan completes |
| PayloadTooLarge | payload_too_large | api | HTTP 413 when a request body exceeds the size limit |
| RateLimited | rate_limited | api | HTTP 429 when the service's own request limit is hit |
| ProviderUnavailable | none (internal) | llm | Caught internally, triggers fallback, may produce degraded scan with reason provider_unavailable |
| QuotaExhausted | none (internal) | llm | Caught internally, produces degraded scan with reason quota_exhausted |
| JudgeOutputInvalid | none (internal) | llm | Retry once, then unreviewed tools and degraded scan with reason judge_output_invalid |
| StorageError | storage_error | storage | HTTP 500, or scan marked failed if the report cannot be persisted; error logged |
| InternalError | internal_error | any | HTTP 500 or scan marked failed with a generic message, details in logs only |

Principle: errors in the optional stage (judge) never fail a scan. Errors in required stages (ingest, rules, aggregation, storage) do.

Error codes are part of the public API contract. New codes may be added; an existing code never changes meaning. Errors during asynchronous processing do not produce an HTTP error: the scan becomes failed and its detail carries failure.code and failure.message. Messages never contain secrets or full scanned payloads.

## 14. Aggregation and Scoring (core.scoring)

1. Collect findings from rules and the judge.
2. Deduplicate: findings with the same tool, category, and overlapping evidence are merged, keeping the highest severity and recording all sources.
3. Per-tool severity is the highest severity among its findings, or none.
4. Scan verdict uses the fail threshold fail_on, taken from the request or from default_fail_on, which is high:
   - fail when any finding has severity at or above fail_on;
   - review when no finding reaches fail_on and the highest severity is at least medium;
   - pass otherwise (the highest severity is low or info, or there are no findings).
   When fail_on is medium or lower, review cannot occur, since every medium finding already fails. Judge annotations that mark a finding as a likely false positive do not change the verdict.
5. The report and the scan response record the threshold used, so verdicts are reproducible.
6. Scoring is fully deterministic given findings and configuration.

## 15. Storage Design (storage)

SQLite with the following tables.

| Table | Key columns |
|---|---|
| scans | id, target, target_type, status, current_stage, fail_on, verdict, ai_review_status, ai_review_reason, degraded_reason, failure_code, created_at, started_at, finished_at, report_json, versions |
| tools | id, scan_id, server_name, name, content_hash, description, schema_json |
| findings | id, scan_id, tool_id, source, rule_id, category, severity, evidence_json, explanation, suggested_action, confidence, review_note |
| judge_cache | content_hash, prompt_version, model_family, verdict_json, provider, model, created_at |
| ai_calls | id, provider, model, called_at, estimated_tokens, input_tokens, output_tokens, outcome (call log for rolling windows, section 10.1) |
| provider_quota | provider, observed_at, limit_requests, remaining_requests, reset_requests, limit_tokens, remaining_tokens, reset_tokens, cooldown_until, reported_daily_used (Groq header snapshot, cooldowns, and the OpenRouter cross-check reading) |
| false_positive_marks | id, rule_id, tool_content_hash, created_at, note (local, optional, later version) |

Design notes: foreign keys enforced, indexes on scan_id, content_hash, and ai_calls by provider with called_at, plus indexes that support the API's list filters and sorts (scans by created_at, findings by scan_id with severity and category). Writes happen at the end of stages that need durability: scan creation, completion, cache writes, and budget updates. Schema versioning is handled by a simple migration approach to be chosen at implementation.

## 16. Pipeline Orchestration (core.pipeline)

### 16.1 State machine
pending, then running, then completed or failed. A completed scan may carry a degraded flag with a reason. There is no partially completed state in the stored status, since degradation is a property of the result.

Progress: the scan records current_stage (a ScanStage value), updated at the start of each stage, so polling clients can show progress. Stages that do not run for a scan are skipped in progress, not shown as failed.

When AI review is disabled, by request or because no provider keys are configured, the triage and judge stages are skipped. ai_review_status is disabled with its reason, and the scan is not degraded.

### 16.2 Stage sequence and rules
1. Ingest. Failure fails the scan.
2. Rules. Failure fails the scan.
3. Triage. Pure function.
4. Cache lookup, batching, judging. Failures degrade.
5. Aggregate and score.
6. Render reports and persist.

### 16.3 Time and size limits
A total scan timeout applies. If it is reached during AI review, the scan finishes with what it has and is marked degraded. Limits on the number of tools per scan protect memory and quota.

### 16.4 Concurrency
Scans run as asynchronous tasks within the service. A small cap limits concurrent scans. All judge calls pass through the shared rate limiter and budget ledger, so concurrent scans cannot exceed provider limits together.

### 16.5 Idempotency
Submitting the same manifest again creates a new scan record, but cache hits make AI cost near zero. Scan IDs are unique and unguessable.

## 17. Reporting Design (reporting)

- Input: ScanReport.
- Output: JSON conforming to a versioned schema, and Markdown following the structure in pdd.md section 7. Both are served through the report endpoint (section 18).
- Safe presentation: evidence blocks are escaped, control and invisible characters shown by name, markup neutralized, and excerpts length-limited.
- Determinism: same input produces identical output apart from timestamps, which helps testing and diffing.

## 18. API Design (api)

The HTTP API is the only interface of the product. The separate frontend (ToolGuard-fe), CI jobs, and scripts are all clients of it, and it must let the frontend attach without backend rework.

### 18.1 Endpoints (version 1)

| Method and path | Purpose | Key inputs | Key outputs |
|---|---|---|---|
| POST /v1/scans | Create a scan | manifest or server_url (exactly one), optional target_token (bearer token for the target server, held in memory only), ai_review true or false (default true), fail_on (default high), optional label | 202 with scan_id, status, current_stage, created_at |
| GET /v1/scans | Scan history | page, page_size, status, verdict, target, sort | Paginated scan summaries |
| GET /v1/scans/{id} | Scan detail and progress | none | Scan summary (section 18.3) |
| GET /v1/scans/{id}/findings | Findings of a scan | page, page_size, severity, min_severity, category, source, tool_name, sort | Paginated findings |
| GET /v1/scans/{id}/tools | Per-tool results | page, page_size, min_severity, sort | Paginated tool results, including tools with no findings |
| GET /v1/scans/{id}/report | Full report | format: json (default) or markdown | Report content; scan_not_ready if not completed |
| GET /v1/rules | Rule catalog | none | Rule IDs, categories, default severities, descriptions |
| GET /v1/budget | AI budget | none | Per provider: used, remaining, and when capacity refills or resets; whether AI review is currently available and, if not, the reason |
| GET /v1/health | Liveness | none | Service status and version |

The findings and tools endpoints serve the frontend's report screen. The report endpoint serves download and archival.

### 18.2 Conventions and contract stability
- All routes live under /v1. Within v1, changes are additive only: new endpoints, new optional parameters, new response fields, and new enum values. Clients must ignore unknown fields and tolerate unknown enum values. Removing or renaming anything requires /v2.
- The OpenAPI schema is generated by FastAPI from typed request and response models. Every route declares its response model and its documented error responses. The schema is committed as a snapshot file, and a test fails when the generated schema differs, so contract changes are always deliberate.
- Field names use snake_case, timestamps are UTC in ISO 8601, enums are lowercase strings, and severity is always the word, never a number or color.
- Scan IDs are unique and unguessable.
- Request size limits and rate limits protect the service.
- Interactive documentation is generated from the schemas.

### 18.3 Scan summary and progress
GET /v1/scans/{id} returns: scan_id, target, label, status, current_stage, completed_stages, fail_on, verdict (once completed), counts by severity and by category, tool_count, top findings (at most five, highest severity first), ai_review_status with reason code and message, degraded and degraded_reason, unreviewed tool count, failure (code and message, only when failed), and timestamps. Scan history items use the same shape without top findings.

Progress uses polling first: clients poll this endpoint until status is completed or failed. The response includes a suggested poll interval. Streaming can be added later as an additional endpoint (for example server-sent events at /v1/scans/{id}/events) that emits the same stage objects as the polling response, so existing clients keep working and new clients reuse the same models.

### 18.4 Pagination, filtering, and sorting
- Offset pagination: page starts at 1, page_size defaults to 50 and is capped by api_max_page_size. List responses have the shape items, page, page_size, total.
- Filters combine with AND. A filter given several values (for example severity=high and severity=critical) combines those values with OR. min_severity selects that severity and above.
- sort takes a field name with an optional leading minus for descending. Findings sort by severity, category, tool_name, or rule_id, default -severity then tool_name. Tools sort by highest severity or name. Scans sort by created_at, default -created_at.
- Unknown filter values or sort fields return invalid_input rather than being ignored.

### 18.5 Error shape
Every error response uses one body shape: an error object with code (a stable machine-readable code from section 13), message (a human-readable sentence following pdd.md section 9), and optional details (for example a list of field problems with location and reason). FastAPI's default validation errors are mapped to this shape with code invalid_input. The shape is part of the OpenAPI schema.

### 18.6 CORS
CORS is off by default. When cors_allowed_origins is set, only those exact origins are allowed, never a wildcard. Allowed methods are GET and POST; allowed headers are Authorization and Content-Type.

### 18.7 Access control
Because every scan can consume the owner's free AI quota, a public deployment must not be open. The optional service access token is sent as a bearer token in the Authorization header and checked on all routes except health. If unset, the service should run only locally or behind a private network. Real user accounts are out of scope. Per-client rate limiting is a later improvement.

### 18.8 CI use
CI is served by the API, not by a separate tool. A pipeline submits a scan with fail_on, polls until the scan completes, and fails the job when verdict is fail. The summary's counts and top findings give the log enough to show which tool and text caused the failure. Pipelines that require full AI review can also fail when degraded is true.

## 19. Frontend Integration Notes

- The frontend is a separate project (ToolGuard-fe) and talks to this backend only through the /v1 API. The backend serves no HTML pages besides the generated API documentation.
- Screen to endpoint mapping (screens from pdd.md section 11): New scan uses POST /v1/scans; Scan progress polls GET /v1/scans/{id}; Report uses the scan summary plus the findings and tools endpoints; History uses GET /v1/scans; Budget uses GET /v1/budget.
- Scanned text in API responses is untrusted. The backend returns it as plain strings with invisible and control characters made visible by name (section 17). The frontend must render it as text, never as HTML or Markdown.
- The Markdown report is meant for download and viewing as plain text, not for rendering scanned content as markup.

## 20. Logging and Observability

- Structured logs per stage with scan ID, stage name, duration, and counts.
- AI call logs include provider, model, estimated and actual tokens, outcome, and fallback path. They never include keys. Scanned content is truncated or hashed in logs.
- Pydantic AI instrumentation emits standard traces that can be sent to any compatible backend later.
- Basic counters: scans, findings by severity, AI calls, cache hits, degraded scans.

## 21. Security Design Details

- Input handling: strict size limits and schema validation on every entry point.
- Network safety: address validation after DNS resolution and on every redirect, no access to internal ranges, tool listing only.
- Judge isolation: no tools registered, fixed output schema, authority rules enforced in code.
- Output safety: report neutralization as described.
- Secrets: environment only, redacted from logs and errors, never stored in the database. A per-scan target token is held in memory only for that scan.
- Dependencies: pinned versions, and the project should review dependency advisories, given recent supply chain incidents in the Python ecosystem.
- Abuse protection: access token and limits as in section 18.3.

## 22. Test Design

| Area | Approach |
|---|---|
| Rules | One positive and one negative fixture per rule, table-driven tests |
| Ingestion | Valid and invalid manifests, size limit tests, address safety tests including redirects |
| Triage | Table-driven tests of the decision policy, the suspicion signals, and the suspicion order under the cap |
| Scoring | Deterministic tests for deduplication, severity, and verdict thresholds |
| Model layer | Fake models to simulate success, rate limits, errors, and invalid output; recorded quota headers parsed correctly; no real quota used |
| Budget ledger | Tests for the Groq refill calculation from a snapshot, rolling windows from the call log, restart without a snapshot, cooldowns, the OpenRouter larger-of cross-check, and exhaustion |
| Judge | Authority rule tests, batching tests, the model loop (provider error then next model, invalid output then next model, all models failing gives a degraded scan), and recorded-response tests |
| Pipeline | End-to-end runs on the fixture corpus with a fake model, including degraded paths |
| API | Contract tests for every endpoint, the OpenAPI schema snapshot, error shapes and codes, pagination, filters and sorting, CORS on and off, and the access token |
| Reports | Golden-file tests and safe presentation tests with hostile text |
| Fixture regression | A pytest suite runs the pipeline with a fake model over the fixture corpus, reports mismatches, and reports detection and false positive rates |
| Live smoke | One rarely run manual test against real providers to confirm structured output, the model loop, and the Groq header refill behavior |

Coverage targets: every rule and every authority rule must have tests. Fixture corpus metrics (detection and false positive rates) are reported by the pytest fixture regression suite.

## 23. Deployment Design

- Container image with configuration from environment variables.
- Single instance in the first release because of SQLite and local counters.
- Persistent volume for the database file.
- Health endpoint for orchestration, budget endpoint for operators.
- Secrets provided by the hosting platform, never baked into the image.

## 24. Implementation Order

This order matches the milestones in instruction.md section 8 and prd.md section 12.

1. M0: Settings, project skeleton, and provider verification spikes.
2. M1: Domain models, manifest ingestion, and fixtures.
3. M2: Rule engine and rule tests.
4. M3: Storage, scan pipeline without AI, and JSON and Markdown reports.
5. M4: Model layer, budget ledger, cache, and fake-model tests.
6. M5: Triage, judge, and authority rules, with degraded handling.
7. M6: HTTP API and the pytest fixture regression harness.
8. M7: Remote ingestion over HTTP, hardening, documentation, and deployment.

## 25. Open Technical Questions

Closed by the M0 spikes (details in spikes/FINDINGS.md):
- Structured output mode: tool output (section 11.2).
- Classifier invocation and input limit: answered, and the classifier stage was removed (section 7).
- Groq reset behavior: a continuously refilling bucket driven by response headers, not a fixed daily reset (section 10.3).
- Validation failures and fallback: handled by the judge's own model loop instead of FallbackModel (section 11.7).
- Case sensitivity of settings names: case-insensitive (section 3).
- Default failure threshold: high (section 14). Degraded-scan exit codes are not applicable, since there is no CLI.

Still open:
1. Whether OpenRouter's free_model_daily_requests counter is shared across all free models. M0 only called one model. Treated as shared until proven otherwise (section 10.4).
2. OpenRouter's daily reset time. The documentation implies the UTC day but does not state it (section 10.4).
3. Calibration of token estimates and judge_output_tokens_per_tool on the real fixture corpus in M5 (sections 10.2 and 11.5).
4. Whether the Groq refill behavior holds for other limits and over longer periods, checked by the live smoke test (section 10.3).
5. Migration approach for the SQLite schema.
6. Which MCP HTTP transports and authentication schemes to support first.
7. Which streaming transport to add for progress later (server-sent events or WebSocket).