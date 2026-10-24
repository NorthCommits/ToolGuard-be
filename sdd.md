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

Settings are read from environment variables and a local .env file using pydantic-settings. The loader validates them at startup and fails fast with a clear message if a value is invalid, including zero or negative values for the M4 AI settings (tpm_groq, the timeouts and waits, and the cache TTL) and an empty judge_model_family. Names in the existing .env (groq_api_key and openrouter_api_key) are kept. pydantic-settings matches names case-insensitively by default, so the lowercase names work (confirmed in M0 and covered by a test).

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
| tpm_groq | Groq tokens per minute cap; used alone until a quota header snapshot exists, and always as a cap | 7,200 |
| ai_request_timeout_seconds | Timeout of one model request | 60 |
| ai_max_wait_seconds | Longest an attempt waits for a rate limit before returning a refusal | 20 |
| ai_default_cooldown_seconds | Cooldown after a 429 without a usable retry-after or reset header | 60 |
| openrouter_reasoning_effort | Reasoning effort sent to the OpenRouter fallback models (none, minimal, low, medium, high, xhigh, max) | low; lower values only after the supported efforts per model are checked |
| judge_model_family | Model family part of the cache key; change it to invalidate cached verdicts | gpt-oss |
| judge_cache_ttl_days | Days a cached verdict stays valid | 30 |
| judge_max_tools_per_call | Batch size cap | small integer, tuned by token budget |
| judge_max_tools_per_scan | Per-scan cap on tools sent to AI review (section 8) | small integer, tuned by quota |
| judge_output_tokens_per_tool | Output allowance per tool in a batch, including reasoning (section 11.5) | about 250, calibrated in M5 |
| judge_reasoning_effort | Reasoning effort requested from the Groq primary model | low |
| default_fail_on | Fail threshold used when a request does not give one | high |
| judge_max_input_tokens | Estimated input token cap per call | tuned to per-minute token limit |
| ingest_max_bytes, ingest_timeout_seconds, ingest_max_redirects | Ingestion limits | conservative values |
| ingest_max_tools | Maximum tools per manifest; going over is payload_too_large | 500 |
| rules_disabled | Rule IDs to turn off. Read from the environment only, never from API requests; an unknown ID fails at startup with a message naming the setting and the ID | empty |
| rules_severity_overrides | Rule ID to severity, replacing the default severity of a rule finding. Environment only, checked like rules_disabled | empty |
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
| title | Optional display title from MCP, included in the hash |
| description | Description text, possibly empty |
| input_schema | JSON schema describing parameters |
| input_schema_missing | True when the input had no schema; the schema is then stored as {} |
| annotations | Optional hints such as read-only or destructive markers |
| content_hash | Hash of the normalized name, title, description, schema, missing flag, and annotations |

Normalization rules: text fields are stored exactly as received. For hashing only, name, title, and description are trimmed, and the canonical JSON (sorted keys) of name, title, description, input schema, the missing flag, and annotations is NFC-normalized and hashed with SHA-256. NFC, not NFKC, keeps look-alike and zero-width characters distinct. The server name is not part of the hash.

### 4.3 Evidence
| Field | Description |
|---|---|
| field | Which part of the tool: name, description, or a schema path |
| text | The matched excerpt |
| start, end | Character (code point) offsets within the field. Clients in UTF-16 environments, such as JavaScript, must convert them. Offsets always refer to the original stored text, never to the neutralized text shown in a report (section 17) |
| context | Short surrounding text |
| offsets_in | What start and end refer to: field_text (the stored text of the field), schema_excerpt (compact JSON of a schema value), or none (a note written by the rule, start equal to end). Set when the report is built |

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
| fingerprint | Tool content hash and rule ID ("hash:rule_id"), for matching a finding across scans. Stored only since M3; change detection and false positive marks will build on it later. Caveat: cross-tool rules (duplicate names, shadowing) depend on the other tools in the scan, so the same fingerprint can appear or disappear without the tool itself changing |

### 4.5 JudgeVerdict (judge output unit)
| Field | Description |
|---|---|
| tool_name | Tool judged |
| risk_level | Judge's overall severity estimate, restricted to the Severity values (validation rejects anything else) |
| added_findings | New findings the judge identifies |
| annotations | FindingAnnotation items on existing findings: finding ID, likely false positive flag, and a note of up to 300 characters |
| reasoning_summary | Short explanation, up to 300 characters |

### 4.6 ScanReport
| Field | Description |
|---|---|
| scan_id, target, started_at, finished_at | Identification and timing |
| status, verdict | Outcome |
| verdict_message, verdict_basis | The pdd.md section 7.4 wording for the verdict, and the rule that decided it, for example "fail: at least one finding is at or above the fail_on threshold (high)." |
| fail_on | Fail threshold used for the verdict |
| tool_count | Number of tools scanned |
| counts | FindingCounts: counts by severity and by category |
| findings | All findings after aggregation, ordered by severity, then tool position, then rule ID |
| finding_groups | One FindingGroup per rule ID: rule, category, severity, tool count, tool names, and finding IDs, ordered by severity then rule ID (section 17) |
| tool_results | ToolResult per tool, including tools with no findings: tool name, content hash, highest severity, finding count, whether AI reviewed it |
| ai_review_status, ai_review_reason, ai_review_message | performed, partial, skipped, or disabled; the reason code; a short message. Flat fields that match the storage columns |
| degraded, degraded_reason | True when AI review was skipped or partial; the reason code |
| unreviewed_tools | Tools that qualified for AI review but did not receive it |
| ai_usage | AIUsage: calls and estimated tokens per provider (ProviderUsage items), cache hits |
| ai_review_coverage | AIReviewCoverage: tools AI-reviewed, tools that qualified but were not reviewed, tools skipped by triage, tools checked by rules only, and a note that tools not reviewed by AI were checked by rules only. Present from M3, with zero AI-reviewed tools until M5 |
| versions | Versions: ToolGuard, rule set, prompt, and report schema versions. prompt is null until the judge exists (M5) |
| disclaimer | Fixed text stating that a result without findings is not a guarantee of safety |

## 5. Ingestion Design (ingest)

### 5.1 Manifest ingestion
Input: a JSON body or file containing a list of tools, optionally with a server name. Behavior: validate shape, enforce size limits, normalize into Tool models, compute hashes. Invalid entries produce a clear error naming the entry index and field. Partial acceptance is not allowed: if any entry is invalid, ingestion fails with a list of all problems.

Accepted shapes:
- a bare array of tools;
- an object with a tools array and an optional server_name (an MCP tools/list result);
- a full JSON-RPC tools/list response, whose result holds that object.

Tool entries accept MCP field names (inputSchema) and ours (input_schema). Unknown fields are ignored; outputSchema is deferred. A missing schema becomes {} with input_schema_missing set. A present schema that is not a JSON object, or an entry with both schema keys, is invalid input. A manifest with zero tools is invalid input. The byte limit (ingest_max_bytes) and the tool limit (ingest_max_tools) raise payload_too_large. A server name supplied with the request overrides the manifest's; the fallback is "unknown".

Nesting is limited to 64 levels of JSON containers (MAX_NESTING_DEPTH), checked without recursion, so the accepted depth does not depend on the Python version; deeper input is invalid input ("nested too deeply"). Lone surrogate escapes, which JSON allows, are kept in the text and hashed safely; TG-HID-001 reports them.

### 5.2 Remote ingestion (HTTP)
Input: a server URL. Behavior: validate the URL, resolve the host, reject private, loopback, link-local, and otherwise internal addresses (checked after resolution and again on every redirect), connect with the MCP SDK over HTTP, request the tool list only, and normalize results. The connection never invokes any tool. Limits: total bytes, total time, redirect count. Failures map to the ingestion error types in section 13.

Authentication: the first version supports unauthenticated servers and an optional bearer token supplied per scan and never stored or logged. Other schemes are deferred.

### 5.3 Deferred
Local stdio servers are not launched in the first version.

## 6. Rule Engine Design (rules)

### 6.1 Rule contract
A rule has: an ID, a category, a default severity, a short description, and a check. The check takes one Tool and a read-only context (all tools in the scan, the tool's text fields to scan, the field where the scan cap cut the text short if any, and the description length threshold) and returns either nothing or one hit: evidence, an explanation, and a suggested action. Rules are pure: no network, no model, no shared mutable state.

The registry (rules/registry.py) builds each Finding from a hit, so rule ID, category, and severity are set in one place, including severity overrides. There is at most one finding per rule per tool, with up to 5 evidence items. Finding IDs have the form tool_index:rule_id and are scan-local. M3 should consider a cross-scan fingerprint (tool content hash plus rule ID) for change detection and false positive marks; it is not built yet. The rule set carries a version (RULESET_VERSION), bumped whenever a rule is added, removed, or changes what it reports. Rules can be disabled or given another severity through rules_disabled and rules_severity_overrides (section 3).

Text scanned by the text rules (injection, hidden characters, exfiltration, cross-tool): the name, title, description, string annotations (such as annotations.title, which MCP uses as a display title), and every string value and property name in input_schema, including default, enum, const, examples, and title. Non-string values are skipped. Text is capped at 100,000 characters per tool so a huge schema cannot slow a scan; text past the cap is never scanned, and TG-SIZ-003 reports that.

Matching views (added after the first held-out batch): text rules match against a folded copy of each field, never the raw text. Folding removes invisible and control characters, turns unusual spaces into plain spaces, maps a small built-in table of Cyrillic and Greek look-alike letters to Latin, and applies Unicode compatibility folding (NFKC) one character at a time, so fullwidth letters and ligatures match. Folding is for matching only and never used for hashing. An index map links every folded character to its original offset, so evidence offsets always point into the stored text and evidence still shows the original text with hidden characters named. Schema text (parameter names, required entries, and other schema strings) also gets a word-split view, where underscores and hyphens between letters or digits, dots between letters, and camelCase boundaries become spaces, so read_all_api_keys reads as read all api keys. Rules match both views and report each original span once. No new dependency is used. Tests check the index map (including zero-width characters removed before a match) and that ordinary Russian, Greek, Arabic, and Hebrew text does not match English rules.

### 6.2 Initial rule catalog

| Rule ID | Category | Default severity | Detects |
|---|---|---|---|
| TG-INJ-001 | injection | high | Directive text aimed at the model within a description |
| TG-INJ-002 | injection | high | Phrasing that tries to override or disregard prior instructions |
| TG-INJ-003 | injection | high | Instructions to hide actions or information from the user |
| TG-INJ-004 | injection | medium | Text telling the model that its earlier guidance or restrictions no longer apply |
| TG-HID-001 | hidden_characters | high | Zero-width or invisible characters in any scanned text |
| TG-HID-002 | hidden_characters | high | Bidirectional control characters |
| TG-HID-003 | hidden_characters | medium | Mixed-script or look-alike characters in tool names |
| TG-EXF-001 | exfiltration | critical | Instruction to send data to an external address |
| TG-EXF-002 | exfiltration | high | References to secrets, keys, credentials, or sensitive files in directives |
| TG-EXF-003 | exfiltration | medium | Large encoded blobs in any scanned text |
| TG-XTL-001 | cross_tool | medium | Description that directs how other tools must be used |
| TG-XTL-002 | cross_tool | medium | Instruction to call another tool before or after this one |
| TG-SCH-001 | excessive_permissions | medium | Unconstrained execution-like parameters such as command, shell, script, code, or sql |
| TG-SCH-002 | excessive_permissions | low | Missing type or description on parameters, or no input schema at all |
| TG-SCH-003 | excessive_permissions | low | Schema accepts arbitrary additional properties |
| TG-SCH-004 | excessive_permissions | low | Destructive-sounding tool without a destructive or read-only hint |
| TG-SCH-005 | excessive_permissions | high | Hint that contradicts a destructive tool name |
| TG-SCH-006 | excessive_permissions | low | Unconstrained locator parameters such as path, file, url, or host |
| TG-SCH-007 | excessive_permissions | medium | Read-only hint on a tool whose name says it writes or changes data |
| TG-NAM-001 | naming | medium | Duplicate or colliding tool names within a scan |
| TG-NAM-002 | naming | medium | Names that closely imitate common tool names |
| TG-SIZ-001 | structure | low | Description longer than the configured threshold |
| TG-SIZ-002 | structure | info | Unusual schema depth or structure |
| TG-SIZ-003 | structure | medium | Tool text exceeded the scan cap and part of it was not scanned |

The catalog is an initial proposal. Thresholds and patterns are tuned against the fixture corpus. Each rule ships with at least one positive fixture and one negative fixture.

Rule notes and recorded decisions:
- TG-HID-001 covers format and control characters, Unicode tag characters (U+E0000 to U+E007F, used for ASCII smuggling), variation selectors, lone surrogates, and blank fillers. It does not report unusual spaces such as the no-break space, which still render as space (they are shown by name in evidence), or a zero width joiner or emoji variation selector directly after an emoji, which is normal emoji composition.
- TG-HID-001 and TG-HID-002 do not report the plain direction marks U+200E, U+200F, and U+061C, because legitimate right-to-left text uses them. TG-HID-002 reports the embedding, override, and isolate controls (U+202A to U+202E, U+2066 to U+2069) used to reorder displayed text.
- TG-HID-003 reports letters from more than one writing system in a tool name (Japanese kana and kanji count as one), and letters whose compatibility form differs, such as fullwidth or mathematical letters and ligatures.
- TG-EXF-001 needs, in one sentence, a send verb, a kind of private data (conversation, messages, secrets, credentials, file contents, and similar), and an outside address. A documentation link or a tool that sends its own message to a webhook is not reported.
- TG-EXF-002 reports sensitive file paths (such as ~/.ssh, .env, .aws/credentials) together with any directive verb, and secret nouns (API key, password, credentials) only together with taking verbs (read, extract, copy, send, and similar). Documentation such as "pass your API key in the Authorization header" is deliberately not an exfiltration finding.
- TG-EXF-003 reports base64 runs of 120 or more characters and hex runs of 128 or more.
- TG-XTL-001 and TG-XTL-002 fire only when the text targets a different tool: one named in the scan, or a name followed by tool, function, or command. "Call this before create_event" refers to the tool itself and is not reported. TG-XTL-001 also reports policy wording addressed to other tools as a group ("all mail tools must", "every other tool should") with a modal verb, without needing a when clause; plain mentions such as "works with all other tools" are not reported.
- TG-SCH-001 (medium) reports parameters whose name contains an execution-like word (command, cmd, shell, script, code, eval, exec, expression, sql, function; function was added after calibration showed it only flags Playwright's browser_evaluate, which takes JavaScript). TG-SCH-006 (low) reports locator words (path, filepath, file, filename, dir, directory, folder, url, uri, endpoint, host). Both match plurals (paths, commands) and accept a string, an untyped value, or an array of strings, without an enum, const, or pattern on the value or its items. "query" is in neither set, because a free-text search query is normal. The split and the removal of "query" were checked on two independent calibration sources (the MCP reference servers, and the Playwright and GitHub servers, see tests/calibration/README.md): on both, unconstrained paths, file names, and search queries were the main source of noise, while execution-like parameters stay rare and worth a human look.
- TG-INJ-004 (medium) needs, in one sentence, wording addressed to the model about its own earlier guidance (for example "you were previously instructed", "originally you did not", "your earlier rules") and wording that it no longer applies (now, no longer, lifted, removed, does not apply). It is narrow on purpose: product changelogs ("this version removes the old limit") and statements about the tool itself are not reported. The official MCP fetch server triggers it with its "originally you did not have internet access ... this tool now grants you internet access" wording. That is accepted as a reviewable finding: the intent is benign, but the wording overrides earlier guidance, and at medium it does not fail a scan at the default high threshold. The judge sees it for context (section 11.1).
- TG-SCH-003 reports only an explicit additionalProperties of true or {}. Leaving it out is the JSON Schema default, and reporting that would add noise to almost every server.
- TG-SCH-004 and TG-SCH-005 treat annotations as claims by the server, not facts. Per the MCP specification (2026-07-28), readOnlyHint defaults to false and destructiveHint to true, and clients should not rely on hints from untrusted servers. Because a missing hint is already treated as destructive, TG-SCH-004 is hygiene (low). Contradicting hints are judged by words in the tool name only, never by description verbs alone (calibration showed Playwright's browser_hide_highlight, whose description starts "Remove", removes a visual highlight, not data). TG-SCH-005 (high): a destructive name word (delete, remove, drop, destroy, purge, wipe, erase, truncate, overwrite, terminate, kill, reset, uninstall, revoke) with readOnlyHint true or destructiveHint false. TG-SCH-007 (medium): a modifying name word (write, create, update, insert, upload, send, post, put, move, rename, append, modify, edit, patch, save, set, publish, deploy) with readOnlyHint true; Playwright's browser_pdf_save is an example. destructiveHint false on a modifying tool is not a contradiction, because it means additive. TG-SCH-004 still uses the opening description as well as the name. Non-boolean hint values count as missing.
- Wording categories widened after the first held-out batch, each checked against all three calibration sources with no new findings: hiding (no need to tell or bring up with the user, stealth adverbs such as quietly or discreetly before an action, without letting the user know); directives (decode or decrypt followed by follow, execute, or obey; notes or labels addressed to the assistant, such as "Note for the assistant:" or "LLM:", together with an action verb); secrets (credential and token file names such as token.json or credentials.yaml, token files under ~/.config, key file names written with separators, and "contents of" or "text of" as a request); exfiltration (third-person and replication verbs such as sends, mirrors, syncs, shares, and host names without a scheme with a common public suffix). Not added, because it could not be generalized without false positives: "add" as an action verb for TG-INJ-001, which would flag ordinary setup text such as "Before using this tool, add the repository to the allowed list".
- TG-NAM-002 compares names against other tools in the scan and a short built-in list of common tool names. It reports only adjacent transpositions, single substitutions inside a word (not the first letter, so get_user and set_user are fine), and look-alike characters. Pairs that differ only by a plural s or a version or numeric suffix are never reported.
- TG-SIZ-002 reports schemas nested more than 10 levels deep or with more than 100 parameters.
- TG-SIZ-003 reports that text past the 100,000 character cap was not scanned, naming the field where scanning stopped and the offset. An instruction hidden after the cap is invisible to the other rules, but this finding (medium) means the scan is never silent about it: the verdict is at least review.

### 6.3 Evidence capture
Rules must record exact positions and excerpts so reports can show the match. Invisible characters are recorded by code point name, not rendered raw.

Format: start and end are character offsets in the stored, unmodified text of the field. The excerpt is limited to 200 characters, and context adds up to 40 characters on each side. In both, hidden and control characters are written as [U+XXXX NAME], for example [U+200B ZERO WIDTH SPACE], so evidence never contains them raw. The field is name, title, description, annotations/<key>, or a JSON Pointer style path into the schema such as input_schema/properties/city/description; a property name is marked with the suffix " (property name)". For structural schema findings with no matched text, the evidence text is the compact JSON of the sub-schema and the offsets cover that text.

### 6.4 Rule performance
All rules together should process a typical manifest in well under a second. No rule may use unbounded backtracking patterns; pattern complexity is reviewed.

Approach: patterns use literal words joined by bounded whitespace runs, and rules that need several words together search inside sentence-like windows of at most 400 characters, so matching stays linear in the text length. The real guard is a timing test that runs every rule on hostile inputs (long runs of repeated characters and near-matches, wide and deep schemas, very long names) with a budget of 1 second per rule and input. A static check of pattern text for nested unbounded quantifiers is only a heuristic. A separate test runs every rule on odd input (empty strings, lone surrogates, control characters, non-string values inside schemas) and requires that no rule raises.

## 7. Classifier Stage (removed from the MVP)

A prompt-injection classifier stage (Groq-hosted Llama Prompt Guard 2) was evaluated in the M0 spikes and removed. It scored a classic injection phrase at 0.9996 but two realistic tool-poisoning descriptions at only 0.0023 and 0.0027, close to a benign description (0.0004). It recognizes jailbreak phrasing, not instructions written as tool documentation, which is what ToolGuard scans. It is also a preview model that may be discontinued at short notice, and it rejects input over 512 tokens. Details are in spikes/FINDINGS.md. Triage relies on rule findings and the deterministic suspicion signals in section 8. Section number 7 is kept so other references stay valid.

## 8. Triage Design (core.triage)

Triage is a pure function with no model calls. The suspicion signals below are built in M5 together with triage, reusing the M2 rule text helpers (including the folded and word-split views); they are not part of M2 because nothing else uses them.

Triage must not rely only on rule findings. The M2 held-out results showed that a tool which evades the rules (first batch: 0 of 9 fully detected before tuning; second batch: 2 of 11 on first run) would also evade a triage that only looks at rule findings, and would then never reach the AI judge. Suspicion signals are therefore deliberately broader and cheaper than rules: they may be noisy, because they only decide what the judge sees, never the verdict.

Inputs: per-tool rule findings, the scan's fail_on threshold, and the suspicion signals below, all computed deterministically from the tool.
Output: for each tool, skip or review, plus a suspicion order used when the per-scan cap applies.

Suspicion signals (weak signals that do not create findings on their own):
- Hidden or folded-away characters: any character that folding removes or changes (invisible, control, look-alike, or compatibility characters), even below rule thresholds.
- Markup-like text: tags such as \<IMPORTANT\>, HTML comments, code fences, fake headings such as "### SYSTEM", or unusual bracketed blocks.
- Second-person and model-addressed wording: you, your, the assistant, the model, the AI, together with modal or imperative verbs, below the thresholds the rules use.
- Encoded-looking strings: shorter base64 or hex runs than TG-EXF-003 reports, percent-escapes, digit-for-letter words (leetspeak), and words mentioning encodings (rot13, base64, decode).
- Parameter names and enum or required values that read as sentences (several words after splitting separators).
- Sensitive nouns anywhere (password, token, key, credential, conversation, history) and addresses or host names.
- Risky capability hints: execution-like or locator parameters, and destructive or modifying tool names.
- Long descriptions above description_length_threshold, and text split across fields (title and description read together).

Interpretation principle: where the policy can be read two ways, the reading that sends more tools to review is used, since a missed risk costs more than an extra AI call. The per-scan cap still applies.

Decision policy (the first matching rule wins):
1. Skip when the tool has a rule finding that already reaches the scan's fail_on threshold, since AI review cannot change the verdict. The tool is still reported as fail-worthy. A high or critical finding below fail_on (for example fail_on is critical) does not skip.
2. Review when the tool has any rule finding.
3. Review when the tool has at least one suspicion signal.
4. Skip otherwise: no rule findings and no suspicion signals.

Suspicion order: tools selected for review are sorted by highest rule finding severity, then the number of suspicion signals, then description length, all descending, with tool name as a stable tie-breaker. When the cap or the budget is hit, the first tools in this order are reviewed and the remainder are listed in unreviewed_tools. When AI review is disabled, triage does not run and unreviewed_tools is empty.

AI review coverage must be visible: the scan summary and the report show how many tools were AI-reviewed, how many were skipped by triage, and how many qualified but were not reviewed (cap, budget, or provider), with a clear note that tools not reviewed by AI were checked by rules only, and rules miss many attacks (see the held-out results).

Quota impact: without a classifier, no tool is skipped on the basis of a low score, so more tools reach the judge. Three controls keep this within free quota:
- The per-scan cap (judge_max_tools_per_scan, default 10) with batching (judge_max_tools_per_call, default 5) bounds a fresh scan to about 2 judge calls, within the PRD target of 3 or fewer.
- The cache (section 12) means an unchanged tool is never judged twice for the same prompt version, so rescans cost close to zero calls.
- The budget ledger (section 10) stops before limits; the binding Groq limit in practice is tokens per day (200K, with a 180K safety budget). At about 3,500 tokens per full batch (section 10.2), that is roughly 50 full batches, or about 25 fresh capped scans per day, before falling back to OpenRouter or degrading. These figures are recalculated after token calibration in M5.

Full-review estimate for the calibration sources (no real calls; section 10.2 formula with 5 tools per call and 250 output tokens per tool):

| Source | Tools | Calls | Estimated tokens | Share of the 180K daily budget | Scans at the cap of 10 |
|---|---|---|---|---|---|
| MCP reference servers (6 servers) | 39 | 11 | about 20K | 11% | 4 |
| Playwright | 72 | 15 | about 31K | 17% | 8 |
| GitHub | 92 | 19 | about 49K | 27% | 10 |

Output, which includes reasoning, is about half of every estimate. The largest single batch is about 5.3K tokens (GitHub), under the 8K per minute limit, but that limit allows only about one such call per minute, so a full review of the GitHub server would take about 20 minutes. M5 decides between raising the per-scan cap and reviewing in suspicion order across several scans, using the cache so each tool is judged once.

## 9. Model Layer (llm.provider)

### 9.1 Responsibilities
Build the ordered model list and per-model settings, enforce rate limits, read provider quota headers into the budget ledger, and translate provider failures into internal results. The model loop itself lives in the judge (section 11.7).

### 9.2 Model list
Order: Groq primary model, then the configured OpenRouter fallback models, skipping any provider without a key. Model identifiers come from configuration. Each model has its own settings: temperature, output limit (section 11.5), and reasoning effort where the model supports it.

Pydantic AI's FallbackModel is not used. The M0 spikes and the framework documentation show that it falls back only on provider API errors: output validation failures raise UnexpectedModelBehavior after the output retries and never reach the next model, and FallbackModel offers no hook to consult the budget ledger before each attempt. A plain loop in the judge covers both cases (section 11.7).

Provider client retries are set to zero (max_retries 0 in the model settings). The Groq and OpenAI clients that Pydantic AI builds otherwise retry twice on their own, which would hide real calls from the ledger and delay moving to the next model.

M4 implementation (llm/provider.py): build_chain(settings) returns the ordered list of ModelSpec items (provider, model name, Pydantic AI model, model settings). Each has temperature 0, max_retries 0, and the request timeout; Groq gets groq_reasoning_effort from judge_reasoning_effort, and OpenRouter models get openrouter_reasoning with openrouter_reasoning_effort. The output limit is set per call by the judge. The Groq SDK takes an httpx client and the OpenAI SDK used for OpenRouter (openai 3.x) takes an httpx2 client; one client per provider is built, each with the response hook.

An attempt is one HTTP request to one model. run_attempt reserves with the ledger, runs the call the judge passes in, classifies the outcome, and settles the ledger. The judge runs its agent with output retries set to 0 (Pydantic AI Agent retries={"output": 0}, or retries=0), so an invalid answer raises after exactly one request and never becomes a second, unreserved request inside an attempt.

### 9.3 Rate limiting
A limiter per provider enforces the per-minute request cap (rpm_groq, rpm_openrouter). For Groq, the tokens-per-minute budget comes from the response headers (section 10): before each call the estimated tokens (section 10.2) must fit in the tokens currently available, otherwise the call waits for the bucket to refill or the loop moves on. Concurrency is capped so batches do not overlap beyond the limits.

Quota headers are read by a response event hook on the HTTP client passed to each provider, so only the model layer sees provider details. Confirmed on the real Groq client by the M4 smoke run (spikes/FINDINGS.md section 10), which also confirmed one request per attempt and reasoning effort low on both providers. The hook keeps only the x-ratelimit-* headers and retry-after, and counts responses per attempt. Durations such as 10m4.8s, 592ms, 2m59.56s, 1h2m, or 2d are parsed; any missing, malformed, negative, or implausibly large value (counts above ten million, durations above seven days, retry-after above one day) discards the whole snapshot, and the ledger falls back to its own counters. A rate limit wait of up to ai_max_wait_seconds is waited out once with asyncio.sleep; a longer one is returned as a refusal so the judge moves on. Ledger database work runs in a worker thread, so the event loop is never blocked.

### 9.4 Failure handling
An attempt returns one of three typed results: Succeeded (the value, requests, and tokens), Refused (a reason and a retry time: provider_disabled, cooling_down, rate_limited, budget_exhausted, too_large, payment_required), or Failed (a kind, the status code, and a cooldown time). Results, log lines, and the database never contain provider error text, which can quote the request (scanned text) or the key.
- 429: Failed rate_limited. Cooldown from retry-after, else the reset of an exhausted bucket in the headers, else ai_default_cooldown_seconds. The judge moves to the next model.
- 402 (credits or balance): Refused payment_required, with a cooldown until at least the next UTC midnight. Not treated as an authentication error.
- 401 (invalid key): Failed auth. The provider is disabled for the process lifetime, and a clear configuration message is logged without the key.
- 403: Failed forbidden, with no cooldown and no disabling. A 403 can be a content moderation block on one input, and scanned text is hostile by design, so a crafted tool description must never be able to switch AI review off for everyone.
- Timeout, connection error, 5xx, or another status: Failed, no cooldown, no retry on the same model; the judge moves to the next model.
- Invalid output: Failed invalid_output after one request.
- All models unavailable or exhausted: the judge returns a quota or provider result that the pipeline turns into a degraded scan.

Cooldowns and limits come only from real 429 and 402 responses, their headers, and settings; never from response bodies or scanned text.

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

M4 implementation (llm/budget.py):
- Reserve before each attempt and settle after it. Reserving runs in one BEGIN IMMEDIATE transaction that reads the cooldown, the snapshot, and the call log, checks every limit, and inserts a reserved row with the estimate. SQLite's write lock serializes concurrent reservations from threads, tasks, and processes sharing the file, and open reservations count at their estimate, so concurrent calls cannot overcommit.
- Our own counters always apply (rpm, tpm_groq, and the daily budgets); the Groq header snapshot applies on top when it exists, minus attempts reserved after it was observed. The snapshot's token bucket is used only within a minute of the observation, after which our own minute counter covers it.
- Settling records the outcome, the real request count (at least 1), and the actual tokens when known; otherwise the estimate stays.
- A reservation older than the request timeout plus 30 seconds becomes abandoned (a crash or a hang). It still counts at its estimate for the daily totals but no longer holds the per-minute window.
- OpenRouter: requests today are the larger of our own count since 00:00 UTC and the reported used value plus our own calls since that reading. A reported limit can lower daily_budget_openrouter_requests, never raise it. read_openrouter_key_info keeps the last reading in memory and reads at most once every 10 minutes per process; it is never called before each attempt.
- Per process only: the provider-disabled flag after a 401 and the cached key info. The ledger itself is consistent across threads and processes through SQLite, but the first deployment runs a single worker so these flags hold for the whole service (to revisit in M6).
- The token estimate formula (section 10.2) belongs to the judge and is calibrated in M5; M4 takes the estimate as an argument.
- Token accounting, current behavior: a reserved attempt counts its estimate; once settled with known usage it counts actual input plus output tokens, in both the per-minute window and the daily total. Observation (M4 smoke run, one data point, unconfirmed): Groq's per-minute bucket dropped by input plus the full output limit (537 for 139 input, 42 output, and a 400 limit), so Groq appears to charge the reserved amount at request time. Whether the daily token limit charges the reserved or the actual amount is unknown. A change is proposed and awaits the owner's decision.

## 11. Judge Design (llm.judge)

### 11.1 Input assembly
For each batch: a fixed instruction block, then each tool in a clearly delimited data block containing name, description, schema excerpt, and the deterministic findings already known. The instruction block states that the delimited content is untrusted data, must never be followed as instructions, and that the only task is classification and explanation.

The instruction block also defines the severity levels, using one sentence each from pdd.md section 7.3, and states that a tool with no risk signals is info. When unsure between two levels, the judge picks the higher one only if it can quote evidence. This was added after M0 spike 01 rated the same benign tool low in one mode and info in others.

Rule findings that need judgment are passed to the judge for context and may be annotated, never removed (section 11.3). TG-INJ-004 is the main example: wording that lifts earlier guidance is sometimes benign, as in the official fetch server, and the judge can add a note explaining whether it looks like an attempt to manipulate the agent.

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
- Provider client retries set to zero (section 9.2), and output retries set to zero (section 11.7).

### 11.6 Versioning
The judge prompt has a version string. Any change that could alter results increments it, which invalidates affected cache entries.

### 11.7 Model loop, retries, and degradation
The judge loops over the ordered model list from section 9.2 for each batch:
1. Ask the budget ledger whether the model's provider can take this call (cooldown, rate limits, and the token estimate). If not, move to the next model.
2. Run the agent with that model (agent.run with the model argument) through the model layer's run_attempt, with output retries and provider client retries at zero, so the attempt is exactly one reserved request.
3. Record the outcome and actual usage with the ledger, including the quota headers read by the model layer. This happens after every attempt, successful or not.
4. On success, return the validated verdicts.
5. On a provider error (ModelAPIError: rate limit, quota, server error, timeout, connection, authentication), apply the failure handling in section 9.4 and move to the next model.
6. On invalid output (UnexpectedModelBehavior, including content filter and incomplete tool call), start a new attempt through the ledger: the same model once, or the next model.
7. If no model succeeds, the batch is marked unreviewed and the scan is flagged degraded with reason quota_exhausted, provider_unavailable, or judge_output_invalid, whichever applied to the last attempt.

One attempt therefore costs exactly one request, reserved before it is made. A validation retry is a new attempt, reserved like any other.

## 12. Cache Design (llm.cache)

- Key: content hash of the tool, judge prompt version, and a coarse model family tag so that a change of primary model can be configured to invalidate results.
- Value: the JudgeVerdict, creation time, and provider and model name used.
- Lookup happens before batching; hits are removed from the batch.
- Retention: entries expire after a configured period or on prompt version change.
- Cache entries are never shared across different judge prompt versions.

M4 implementation (llm/cache.py, table judge_cache): the key is (content_hash, prompt_version, judge_model_family). The value is the JudgeVerdict JSON, the actual provider and model, and created_at. get() treats an entry older than judge_cache_ttl_days, or one that no longer validates, as a miss; put() deletes expired entries. The prompt version is a required argument; it does not exist until M5, so tests use a fixed test version. Until M5 decides otherwise, put() stores only verdicts from the configured primary model (Groq and primary_model) and returns not_primary_model for any other, so a fallback model's verdict is never served later as the primary one.

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

Error codes are part of the public API contract. New codes may be added; an existing code never changes meaning. Errors during asynchronous processing do not produce an HTTP error: the scan becomes failed and its detail carries failure.code and failure.message (stored in the scans columns failure_code and failure_message). Messages never contain secrets or full scanned payloads.

Failure messages are generic: they never contain scanned text, the target, or paths from the input. Ingest messages state only limits and positions; storage_error and internal_error use fixed messages, and details go to the log as the exception type only, because exception text can quote scanned content.

The scan target is untrusted text of 1 to 500 characters, rejected with invalid_input at submission otherwise, and is neutralized wherever it is shown. In M3 the caller supplies it, with "manifest" as the default, and target_type is always "manifest". Requirement for M7: when targets can be URLs, credentials and query strings are stripped before the target is stored or shown, and the per-scan target_token is never stored.

## 14. Aggregation and Scoring (core.scoring)

1. Collect findings from rules and the judge. The verdict function (core/scoring.py) exists from M2 so the fixture regression suite can check expected verdicts; deduplication and counts come in M3.
2. Deduplicate. Rule findings with different rule IDs are never merged, even in the same category, because each rule carries its own explanation and fixture expectations. Since each rule reports at most one finding per tool, deduplication in M3 is a guard on the finding ID (tool position and rule ID) that keeps the highest severity. Merging across sources (a judge finding that repeats a rule finding) comes in M5, together with a sources field on Finding.
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
| scans | id, target, target_type, status, current_stage, fail_on, verdict, ai_review_status, ai_review_reason, degraded_reason, failure_code, failure_message, created_at, started_at, finished_at, report_json, versions |
| tools | id, scan_id, position, server_name, name, title, content_hash, description, schema_json, schema_missing, annotations_json (unique per scan_id and position, since tool names need not be unique) |
| findings | scan_id and id (together the primary key; id is the scan-local "position:rule_id"), tool_id, source, rule_id, category, severity, evidence_json, explanation, suggested_action, confidence, review_note, fingerprint |
| judge_cache | content_hash, prompt_version, model_family (together the primary key), verdict_json, provider, model, created_at |
| ai_calls | id, provider, model, status (reserved, settled, or abandoned), reserved_at, finished_at, outcome, requests, estimated_tokens, input_tokens, output_tokens (call log for rolling windows, section 10.1; indexed by provider and reserved_at) |
| provider_quota | provider, observed_at, limit_requests, remaining_requests, reset_requests, limit_tokens, remaining_tokens, reset_tokens, cooldown_until, reported_daily_used, reported_daily_limit, reported_at (Groq header snapshot, cooldowns, and the OpenRouter cross-check reading) |
| false_positive_marks | id, rule_id, tool_content_hash, created_at, note (local, optional, later version) |

Design notes: foreign keys enforced, indexes on scan_id, content_hash, and ai_calls by provider with called_at, plus indexes that support the API's list filters and sorts (scans by created_at, findings by scan_id with severity and category, findings by fingerprint). Writes happen at the end of stages that need durability: scan creation, completion, cache writes, and budget updates. The M3 tables are scans, tools, and findings (migration 1). Migration 2 (M4) adds ai_calls, provider_quota, and judge_cache, with ledger times stored as UTC seconds since the epoch. false_positive_marks comes later.

Access (M3): the standard library sqlite3 module, no extra dependency. Every operation opens its own short-lived connection with foreign keys on and a 5 second busy timeout, and every write is one explicit BEGIN IMMEDIATE transaction, so threads never share a connection. Tools, findings, and the report of a scan are written in one transaction. The file uses WAL mode so reads and a write can overlap; if another connection holds a lock when a connection tries to switch a new file to WAL, it keeps the current mode, since correctness does not depend on WAL. One process only (section 23). The Markdown report is not stored; it is rendered from report_json, which is deterministic. The database file and its -wal, -shm, and -journal side files are ignored by git, since all of them can hold scanned text.

Migrations: an ordered list of statement lists in storage/db.py, tracked by PRAGMA user_version. On connect, all missing migrations run in one explicit transaction; on any error the transaction rolls back completely and user_version is unchanged. Statements run one at a time, because executescript() commits any open transaction first. A database whose version is newer than the code is refused with storage_error. Chosen because it is a few lines, needs no library, and is enough for one process with a local file; later milestones add migrations to the list.

Stored content and retention: tool names, titles, descriptions, schemas, and annotations are stored as received, and may contain any text a server published, including text that looks like a secret. They are never executed and are shown only as neutralized, length-limited excerpts. UTF-8 cannot hold a lone surrogate (valid as a JSON escape), so in text columns a lone surrogate becomes U+FFFD, one character for one, which keeps offsets valid; JSON columns keep it exactly as an escape, and content_hash is computed from the original. No settings, keys, or tokens are stored. Scans are kept indefinitely for now; a delete mechanism (an endpoint or a retention setting) is a candidate for M6.

## 16. Pipeline Orchestration (core.pipeline)

### 16.1 State machine
pending, then running, then completed or failed. A completed scan may carry a degraded flag with a reason. There is no partially completed state in the stored status, since degradation is a property of the result.

Progress: the scan records current_stage (a ScanStage value), updated at the start of each stage, so polling clients can show progress. Stages that do not run for a scan are skipped in progress, not shown as failed.

When AI review is disabled, by request or because no provider keys are configured, the triage and judge stages are skipped. ai_review_status is disabled with its reason, and the scan is not degraded.

M3: the pipeline runs synchronously in core/pipeline.py and writes current_stage as queued (at creation), then ingest, rules, scoring (which includes aggregation), reporting, and done. triage and ai_review are skipped. run_scan takes ai_review: false gives the reason disabled_by_request; true without provider keys gives no_provider_keys; true with keys raises an internal error and marks the scan failed, since there is no AI stage yet and a report must never look as if AI review ran. A failure in a stage marks the scan failed with a stable code (invalid_input or payload_too_large from ingest, storage_error for database errors, internal_error otherwise), and current_stage stays at the stage that failed.

### 16.2 Stage sequence and rules
1. Ingest. Failure fails the scan.
2. Rules. Failure fails the scan.
3. Triage. Pure function.
4. Cache lookup, batching, judging. Failures degrade.
5. Aggregate and score.
6. Render reports and persist.

### 16.3 Time and size limits
A total scan timeout applies. If it is reached during AI review, the scan finishes with what it has and is marked degraded. Limits on the number of tools per scan protect memory and quota. M3 has no slow stage, so the timeout is added in M5 together with the AI stage.

### 16.4 Concurrency
Scans run as asynchronous tasks within the service. A small cap limits concurrent scans. All judge calls pass through the shared rate limiter and budget ledger, so concurrent scans cannot exceed provider limits together.

### 16.5 Idempotency
Submitting the same manifest again creates a new scan record, but cache hits make AI cost near zero. Scan IDs are unique and unguessable.

## 17. Reporting Design (reporting)

- Input: ScanReport.
- Grouping (requirement for M3): when the same rule fires on many tools of one scan, summaries and the Markdown report group those findings under one entry with a count and the list of tools, so twelve identical schema findings do not bury the important ones. Individual findings stay available in full (JSON report and the findings API).
- Output: JSON conforming to a versioned schema, and Markdown following the structure in pdd.md section 7. Both are served through the report endpoint (section 18).
- Grouping in M3: the JSON report has finding_groups, one per rule ID. In Markdown, a rule that fires on 3 or more tools gets one grouped card that lists up to 20 tools ("and N more" after that) and shows one example finding in full.
- Safe presentation: evidence blocks are escaped, control and invisible characters shown by name, markup neutralized, and excerpts length-limited.
- Determinism: same input produces identical output apart from timestamps, which helps testing and diffing. Ordering is stable everywhere, and JSON keys are sorted.

Safe presentation in detail (M3, reporting/safe.py and the two renderers):
- Untrusted values: everything that comes from a scan, including the target, tool names, field paths, evidence, context, and rule explanations (which quote tool and property names). The report is neutralized once, and the neutralized report is what is stored in report_json and rendered.
- Neutralizing: hidden and control characters are shown by code point and name (for example [U+202E RIGHT-TO-LEFT OVERRIDE]). Length limits: target 500 characters, names 100, field paths 200, prose 600, evidence 200, and 40 original characters of context on each side.
- Shortening is never silent: every cut adds "[... N more characters]", where N counts characters of the original text, in both formats. Evidence is rebuilt from the stored tool text at the finding's offsets, so the count is exact even when a rule had already cut its excerpt.
- Markdown: only template text is unescaped. Every scanned value is first made one line of printable ASCII (other characters written as [U+XXXX]), then placed in an inline code span (names, paths, target) or a fenced block (evidence) whose backtick delimiter is longer than any backtick run inside, or written as prose (explanations) with link triggers defanged ("://" as "[:]//", "@" as "[@]", and a dot between letters or digits as "[.]", which covers "www." and bare domains) and every ASCII punctuation mark backslash-escaped. Defanging matters because a renderer may detect autolinks after escapes are removed, and some renderers link bare domains. No scanned value is placed in a table.
- Known limitation (M3): Markdown shows every non-ASCII character as [U+XXXX]. This is safe and makes look-alike letters visible, but reports for servers written in other languages are hard to read. The JSON report is unaffected and keeps the original text (with only hidden characters named). To revisit in M6 with the frontend.
- JSON: written with ensure_ascii and sorted keys, and with <, >, and & escaped as \u003c, \u003e, and \u0026, so it is safe to embed in an HTML page.

JSON report schema notes (report_schema "1"; adding a field does not change the version, removing or redefining one does):
- Evidence start and end always refer to the original stored text of the field. The neutralized evidence text can be longer or shorter than end minus start, because hidden characters are replaced by names and long excerpts are cut with a marker.
- Exceptions: for structural evidence (compact JSON of a schema value, such as a parameter definition), start and end cover that JSON text, not stored text; evidence written by a rule as a note, such as "(no input schema)" or the scan-cap note, has start equal to end. Each evidence item says which case applies in offsets_in (field_text, schema_excerpt, or none), and the Markdown report says it next to the offsets, for example "characters 0 to 79 of the schema excerpt".

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
- The API never serves database rows directly. Stored tool text and finding explanations are kept as received (section 15), so every response that carries scanned text is built from the neutralized report (section 17), never from the tools or findings tables.
- Evidence offsets (start, end) are code point positions in the original stored text, not in the neutralized text shown, which can differ in length. Exception: structural evidence (compact JSON of a schema value) has offsets that cover that JSON text, and rule notes have start equal to end; clients must not use those offsets to highlight stored text (section 17). Clients tell the cases apart by the evidence field offsets_in.

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
Because every scan can consume the owner's free AI quota, a public deployment must not be open. The optional service access token is sent as a bearer token in the Authorization header and checked on all routes except health. If unset, the service should run only locally or behind a private network. Real user accounts are out of scope. Every scan can spend the free AI quota, so the access token and per-client request limits must both be in place before any public deployment (M6). The first deployment runs a single worker, because the provider-disabled flag and the cached OpenRouter key info are per process (section 10.5).

### 18.8 CI use
CI is served by the API, not by a separate tool. A pipeline submits a scan with fail_on, polls until the scan completes, and fails the job when verdict is fail. The summary's counts and top findings give the log enough to show which tool and text caused the failure. Pipelines that require full AI review can also fail when degraded is true.

## 19. Frontend Integration Notes

- The frontend is a separate project (ToolGuard-fe) and talks to this backend only through the /v1 API. The backend serves no HTML pages besides the generated API documentation.
- Screen to endpoint mapping (screens from pdd.md section 11): New scan uses POST /v1/scans; Scan progress polls GET /v1/scans/{id}; Report uses the scan summary plus the findings and tools endpoints; History uses GET /v1/scans; Budget uses GET /v1/budget.
- Scanned text in API responses is untrusted. The backend returns it as plain strings with invisible and control characters made visible by name (section 17). The frontend must render it as text, never as HTML or Markdown.
- The Markdown report is meant for download and viewing as plain text, not for rendering scanned content as markup.
- Highlighting evidence: offsets (start, end) are code point positions in the original stored text, not in the neutralized text shown, which can differ in length. Exception: structural evidence (compact JSON of a schema value) has offsets that cover that JSON text, and rule notes have start equal to end; clients must not use those offsets to highlight stored text (section 17). Clients tell the cases apart by the evidence field offsets_in. Offsets are code points, so JavaScript must convert them from UTF-16 indices.
- The Markdown report shows non-ASCII characters as [U+XXXX] (section 17); the frontend should build its views from the JSON or the API, which keep the original text.

## 20. Logging and Observability

- Structured logs per stage with scan ID, stage name, duration, and counts.
- AI call logs include provider, model, estimated and actual tokens, outcome, and fallback path. They never include keys. Scanned content is truncated or hashed in logs. Since M4, each attempt logs one line: provider, model, outcome, status code, duration, requests, and estimated and actual tokens. Exceptions from providers are never logged, since their text can quote the request.
- HTTP library loggers: when the app configures logging (M6), the httpx, httpcore, httpx2, and httpcore2 loggers are set to warning level by default, because at info level they log every request with its full URL. No URL with credentials or a query string is ever logged; this matters from M7, when scanned target URLs may carry tokens in query strings. The M4 smoke run showed such request lines because the script silenced only the httpx logger.
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
| Budget ledger | Tests for the Groq refill calculation from a snapshot, rolling windows from the call log, restart without a snapshot, cooldowns, the OpenRouter larger-of cross-check, and exhaustion. M4 adds an injected clock, concurrent reservations from threads and tasks, abandoned reservations, UTC day boundaries, and reported limits that may lower but never raise the budget |
| Model layer (M4) | The real Groq and OpenRouter providers over mock HTTP transports (httpx and httpx2): success with quota headers, 429 with and without usable headers, 401, 402, 403 from hostile input, 5xx, timeouts, connection errors, invalid output in one request, malformed headers, and a test that provider error bodies and fake keys never reach results, logs, or the database. An autouse fixture blocks name lookups and socket connections, so no test can reach a real host |
| Cache (M4) | Hit, miss by hash, prompt version, and family, expiry and purge, corrupt entries, and refusal of verdicts not from the primary model |
| Judge | Authority rule tests, batching tests, the model loop (provider error then next model, invalid output then next model, all models failing gives a degraded scan), and recorded-response tests |
| Pipeline | End-to-end runs on the fixture corpus with a fake model, including degraded paths |
| API | Contract tests for every endpoint, the OpenAPI schema snapshot, error shapes and codes, pagination, filters and sorting, CORS on and off, and the access token |
| Reports | Golden-file tests and safe presentation tests with hostile text. Golden files in tests/golden/ (ASCII-only, regenerated with UPDATE_GOLDEN=1) pin the scan ID, timestamps, and ToolGuard version. Hostile-text tests cover markdown links and images, HTML, autolinks, fence terminators, table pipes, very long lines, and bidirectional characters, in descriptions, tool names, and property names, so explanations and field paths are covered too. Every report is checked structurally and by parsing it with markdown-it-py and linkify-it-py (dev-only dependencies) for link, image, and HTML tokens, including bare-URL and email autolinks. The parser check is stricter than GitHub: linkify runs after escaped text is joined, and it also links bare domains and protocol-relative addresses. It does not run GitHub's own renderer |
| Storage | Migrations on a fresh database, twice, rolled back completely when one fails, and refused when the schema is newer; foreign keys and cascades; round trip; concurrent scans from threads |
| Fixture regression | A pytest suite runs the pipeline with a fake model over the fixture corpus, reports mismatches, and reports detection and false positive rates |
| Live smoke | One rarely run manual test against real providers to confirm structured output, the model loop, and the Groq header refill behavior |

Coverage targets: every rule and every authority rule must have tests. Fixture corpus metrics (detection and false positive rates) are reported by the pytest fixture regression suite.

Fixture format and metric definitions: tests/fixtures/README.md. The regression suite ran from M2 on rule output and, since M3, runs the full pipeline (without AI), printing after every pytest run the detection rate, the false positive rate, and for information the number of benign fixtures with any finding. The switch did not change any metric. A separate test runs the pipeline over every fixture, the held-out set, and the calibration manifests as a crash and safety check only, with no metrics.

Held-out hostile set: tests/fixtures/heldout/ holds fixtures written after the rules, from publicly documented MCP attack patterns, to measure overfitting. Its results are printed after every pytest run, separately from the main detection rate, and are not asserted. A batch that has been used for tuning moves into malicious/ as regression tests and is replaced by a fresh batch, whose first run is the honest held-out number. The same author writes the rules and the fixtures, so held-out results are an estimate, not a guarantee.

Calibration: tests/calibration/ holds tool lists of real servers from two independent sources. A script (not part of the pass or fail checks) prints every finding so false positives can be judged by eye before any rule is tuned.

Rule safety tests (section 6.4): timing on hostile input, a nested-quantifier heuristic, and no exceptions on odd input. A test also keeps all .py and .json files ASCII-only, so hidden and look-alike characters in fixtures and tests are always written as escapes and visible in review.

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
7. M6: HTTP API. The fixture regression suite exists from M2.
8. M7: Remote ingestion over HTTP, hardening, documentation, and deployment.

## 25. Open Technical Questions

Closed by the M0 spikes (details in spikes/FINDINGS.md):
- Structured output mode: tool output (section 11.2).
- Classifier invocation and input limit: answered, and the classifier stage was removed (section 7).
- Groq reset behavior: a continuously refilling bucket driven by response headers, not a fixed daily reset (section 10.3).
- Validation failures and fallback: handled by the judge's own model loop instead of FallbackModel (section 11.7).
- Case sensitivity of settings names: case-insensitive (section 3).
- Default failure threshold: high (section 14). Degraded-scan exit codes are not applicable, since there is no CLI.

Closed in M3:
- Migration approach for the SQLite schema: ordered statement lists tracked by PRAGMA user_version (section 15).

Still open:
1. Whether OpenRouter's free_model_daily_requests counter is shared across all free models. M0 only called one model. Treated as shared until proven otherwise (section 10.4).
2. OpenRouter's daily reset time. Since October 2026 the documentation describes free_model_daily_requests as counted per current UTC day, with used, limit, and remaining (section 10.4); the M4 smoke run confirmed that shape, but the reset has not been observed across midnight, and the counter lag and the shared pool are still unmeasured (spikes/06_m4_smoke.py --key-info-only reads the counter again at no quota cost).
3. Calibration of token estimates and judge_output_tokens_per_tool on the real fixture corpus in M5 (sections 10.2 and 11.5).
4. Whether the Groq refill behavior holds for other limits and over longer periods, checked by the live smoke test (section 10.3). The M4 smoke run confirmed the refill of one request every 86.4 seconds on the real client.
5. Which MCP HTTP transports and authentication schemes to support first.
6. Which streaming transport to add for progress later (server-sent events or WebSocket).
7. Which reasoning efforts each OpenRouter fallback model supports. OpenRouter's public model listing has no reasoning effort field for either model (M4 smoke run), so openrouter_reasoning_effort stays low.
8. Whether Groq's daily token limit charges the reserved amount (input plus the output limit) or the actual tokens (section 10.5).