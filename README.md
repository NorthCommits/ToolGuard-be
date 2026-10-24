# ToolGuard

A security scanner and regression harness for the tools that MCP (Model Context Protocol) servers expose.

[![CI](https://github.com/NorthCommits/ToolGuard-be/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/NorthCommits/ToolGuard-be/actions/workflows/ci.yml)

**Status: work in progress (WIP).** Milestones M0 to M3 are done. A manifest can be scanned end to end without AI from Python code, producing JSON and Markdown reports, but there is no HTTP API yet. The next milestone is M4: the model layer, budget ledger, and cache.

## What ToolGuard is

MCP servers describe their tools with names, descriptions, and input schemas, and AI agents read and trust that text. ToolGuard inspects those tool definitions for risky or malicious patterns, such as hidden instructions aimed at the model, data exfiltration, invisible characters, cross-tool manipulation, and overly broad parameters. Every finding is meant to show the exact text that triggered it and explain why it matters.

This repository (ToolGuard-be) is the backend. Its versioned HTTP API will be the only interface of the product. A separate frontend (ToolGuard-fe) will attach to it later.

## What it is not

- It reports risk signals, not proof of safety. A clean result means no issues were detected by these checks, nothing more.
- It never runs scanned servers and never invokes their tools. It only reads tool definitions.
- It does not launch local stdio MCP servers.

## How it works

Scans run as a layered pipeline. Deterministic rules check every tool first, at no AI cost. A deterministic triage step then picks the tools that still look suspicious and orders them within a per-scan cap. Only those tools go to an AI judge, which can add findings or raise severity but can never remove or downgrade a high or critical rule finding. When free AI quota runs out, a provider is down, or no provider keys are configured, the scan still completes with rule-based results and says clearly that AI review was skipped or disabled.

## Roadmap and status

| Milestone | Status | Description |
|---|---|---|
| M0 | Done | Project setup, settings, and provider verification spikes |
| M1 | Done | Domain models, manifest ingestion, and the first fixture corpus |
| M2 | Done | Deterministic rule engine with evidence positions |
| M3 | Done | SQLite storage, the scan pipeline without AI, and JSON and Markdown reports |
| M4 | Planned | Model layer, budget ledger, and cache, tested with fake models |
| M5 | Planned | Deterministic triage and the AI judge with its model loop |
| M6 | Planned | Frontend-ready HTTP API |
| M7 | Planned | Remote MCP ingestion over HTTP, hardening, and deployment |

### Completed

- M0: uv project with pinned dependencies, ruff, and pytest; the empty package structure; a settings module with optional provider keys and offline tests; five manual spike scripts and their findings (spikes/FINDINGS.md), which settled the judge output mode, removed the classifier stage, and shaped the quota design.
- M1: typed domain models (tool, finding, evidence, judge verdict, scan report); manifest ingestion that accepts a bare tool array, an MCP tools/list result, or a full JSON-RPC response, reports all problems at once, enforces size and tool-count limits, and computes a content hash; a first fixture corpus of 2 benign and 6 malicious manifests.
- M2: a deterministic rule engine with 24 rules (the sdd.md catalog, including TG-SCH-005 and TG-SCH-007 for misleading hints, TG-SCH-006 for unconstrained paths and addresses, TG-SIZ-003 for text past the scan cap, and TG-INJ-004 for text that lifts the model's earlier guidance) covering injection, hidden characters, exfiltration, cross-tool manipulation, schema weakness, naming, and structure. Text rules scan the name, title, description, annotations, and every string and property name in the input schema. Text rules match a folded copy of the text (invisible characters removed, look-alike letters mapped to Latin, compatibility forms folded, and schema words split at underscores and camelCase), while findings carry exact character offsets into the original text, short context, and invisible characters shown by name. Rules can be disabled or given another severity through settings. The fixture corpus grew to 5 benign and 26 malicious manifests, and a pytest regression suite reports the detection and false positive rates. A held-out set of hostile fixtures, written after the rules, measures overfitting and is reported on its own: the first batch (0 of 9 detected on its first run) was used for tuning and moved into the regression tests, and the current batch of 11 was detected 2 of 11 on its first run. Calibration manifests from real servers (the MCP reference servers, Playwright, and GitHub) are used to judge false positives by eye. Tests check every rule for speed on hostile input and for robustness on odd input.
- M3: SQLite storage for scans, tools, and findings (standard library sqlite3, migrations tracked by the schema version, one transaction per write); a synchronous scan pipeline that runs ingestion, rules, aggregation, scoring, and reporting without AI, records the current stage, and marks a scan failed with a stable error code and a generic message; deduplication, per-tool results, counts, per-rule finding groups, and a stored cross-scan fingerprint per finding; versioned JSON and Markdown reports following pdd.md, with the verdict and the rule behind it, an AI review coverage block that says rules only, and the not-a-guarantee disclaimer. Scanned text is neutralized in both formats: hidden characters shown by name, every cut marked with the number of omitted characters, evidence in code blocks that scanned text cannot close, links defanged, and nothing scanned in tables. Hostile-text tests check both formats, including by parsing the Markdown, and golden files pin the output. The fixture regression suite now runs the full pipeline, with unchanged metrics.
- CI: a GitHub Actions workflow runs ruff and pytest on Python 3.11 and 3.12 for every pull request to main and every push to main.

### Work in progress

- Nothing is in progress. M4 (model layer, budget ledger, and cache) is the next milestone.

### Later stages

- M4 to M7 as listed in the table.
- A way to delete stored scans (candidate for M6).
- After the first version: change detection between scans, sandboxed scanning of local servers, a GitHub Action that calls the API, and streaming scan progress.

## Tech stack

- Python 3.11 or newer (the project pins 3.12), managed with uv
- FastAPI for the HTTP API (planned, M6)
- Pydantic AI for the judge and model access, pydantic-settings for configuration
- Official MCP Python SDK for remote ingestion (planned, M7)
- SQLite for storage, through the standard library sqlite3 module
- pytest and ruff, plus markdown-it-py and linkify-it-py (development only) to check that reports render without links or HTML
- Model providers: Groq free tier (primary) and OpenRouter free models (fallback)

## Getting started (development)

There is no API or scan command yet; scans can only be run from Python code (toolguard.core.pipeline). These steps set up the project and run the tests.

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Provider keys are optional and are not needed for the tests. To add them, copy the example file and fill in the values:

```
cp .env.example .env
```

- Both keys (groq_api_key and openrouter_api_key) are optional. Without them, scans will run rules-only.
- Names are matched case-insensitively, so GROQ_API_KEY also works.
- .env is ignored by git. Never commit it.

The tests never call a real provider. The scripts in spikes/ do make real calls; they print their call count and ask for confirmation first.

## Continuous integration

.github/workflows/ci.yml runs on every pull request to main and every push to main, on Python 3.11 and 3.12:

- uv sync --locked, which fails if uv.lock is out of date
- ruff check
- ruff format --check
- pytest

It uses read-only permissions, needs no secrets or provider keys, and makes no real provider calls.

## Project structure

```
ToolGuard-be/
  README.md
  .github/workflows/ci.yml  lint and test workflow
  prd.md  architecture.md  sdd.md  pdd.md  instruction.md
  pyproject.toml  uv.lock  .python-version  .env.example
  src/toolguard/
    config.py           settings from the environment
    core/               models.py (domain models), errors.py (error codes),
                        scoring.py (aggregation and verdict), pipeline.py (scan pipeline)
    ingest/             manifest.py (manifest ingestion and content hashing)
    rules/              registry.py (engine and catalog), one module per rule category
    reporting/          safe.py (neutralizing scanned text), json_report.py, markdown.py
    storage/            db.py (SQLite tables, migrations, and reads and writes)
    api/  llm/
  tests/
    conftest.py         shared fixtures; prints the fixture corpus metrics after each run
    fixture_corpus.py   runs the fixtures through the pipeline and computes the metrics
    report_safety.py    checks that a rendered report cannot act as links or HTML
    unit/               settings, models, manifest, scoring, storage, safe presentation,
                        fixture, and regression tests
      rules/            positive and negative cases for every rule, plus speed and robustness tests
    integration/        end-to-end pipeline runs, failure paths, and golden-file report tests
    golden/             expected JSON and Markdown reports
    fixtures/
      benign/           known-good manifests
      malicious/        manifests with expected findings
      heldout/          hostile manifests written after the rules, reported separately
    calibration/        real servers' tool lists, licenses, and a script that prints all findings
  spikes/               M0 verification scripts and FINDINGS.md
```

The api and llm packages are still empty. They are filled in by milestones M4 to M7.

## Documentation

- prd.md: what to build and why, scope, requirements, and milestones.
- architecture.md: system shape, components, data flow, and key decisions.
- sdd.md: detailed design, including models, ingestion, the rule catalog, quota handling, the API, and test design.
- pdd.md: product experience, report layout, severity meanings, and message wording.
- instruction.md: working rules and the milestone plan for development.
- spikes/FINDINGS.md: results of the M0 provider spikes and the decisions they led to.

## Fixtures and regression metrics

tests/fixtures/ holds JSON manifests with the expected verdict and findings for each. A pytest regression suite runs the rules over them and prints the detection rate on malicious/ fixtures, the false positive rate on benign/ fixtures only, and, for information, how many benign fixtures had any finding. Since M3 the suite runs the full pipeline without AI. The held-out set in tests/fixtures/heldout/ is reported separately and not counted in these rates.

Limitation: the same author wrote the rules and the fixtures, so the main detection rate mostly shows that the rules match the cases they were written for. The held-out set is a better estimate of real coverage, but it is still an estimate, not a guarantee: on its first run the current batch was detected 2 of 11. Rules miss obfuscated and novel wording by design; the AI judge (M5) is meant to catch what they miss, and reports show which tools were checked by rules only. See tests/fixtures/README.md for the format and the exact definitions, and tests/calibration/README.md for the calibration sources.

In short: the main corpus metrics (currently 100 percent detection, 0 percent false positives) are measured on fixtures written together with the rules, so they are not a measure of real-world coverage. The fresh held-out batch is the more honest number: 2 of 11 fully detected.

## Example reports

docs/examples/ holds the JSON and Markdown reports for the hidden_instructions fixture. They are generated output, not hand-written. There is no API yet, so they are produced by the same function as the golden-file tests (tests/integration/test_report_golden.py), with the scan ID, timestamps, and ToolGuard version pinned. A test fails if they drift; regenerate them with `UPDATE_GOLDEN=1 uv run pytest tests/integration/test_report_golden.py`.

Known limitation: the Markdown report shows every non-ASCII character as a code point such as [U+00E9]. This keeps it safe and makes look-alike letters visible, but reports for servers written in other languages are hard to read. The JSON report keeps the original text. This will be revisited with the frontend in M6.

## Free tier note

ToolGuard is designed to run on free model quotas only. As read from provider documentation in October 2026, Groq's free tier allows about 1,000 requests and 200,000 tokens per day for the judge model (the request quota refills continuously), and OpenRouter's free models allow 50 requests per day without purchased credits. These limits change. When AI quota is unavailable or keys are missing, scans degrade to rules-only results and the report says so.

## Security

- Scanned content (tool names, descriptions, schemas) is treated as untrusted data. Ingestion only parses it as JSON text, and error messages never repeat it. It is never executed or followed as instructions, and reports neutralize it before display: hidden characters are shown by name, cuts are marked, markup and links are neutralized, and evidence sits in code blocks it cannot close.
- Stored scans keep tool text as received, which can include anything a server published. The database file and its side files are ignored by git. Scans are kept until the database file is deleted.
- No keys or secrets are stored in this repository. Keys come from environment variables only.
- The malicious fixtures are hostile test data with fake values only; see tests/fixtures/README.md.

To report a security issue: CONTACT DETAILS TO BE ADDED.

## License

LICENSE TO BE DECIDED. No license has been chosen yet.
