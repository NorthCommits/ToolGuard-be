# ToolGuard

A security scanner and regression harness for the tools that MCP (Model Context Protocol) servers expose.

[![CI](https://github.com/NorthCommits/ToolGuard-be/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/NorthCommits/ToolGuard-be/actions/workflows/ci.yml)

**Status: work in progress (WIP).** Milestones M0 and M1 are done. The next milestone is M2, the deterministic rule engine. There is no API and no way to run a scan yet.

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
| M2 | Planned | Deterministic rule engine with evidence positions |
| M3 | Planned | SQLite storage, the scan pipeline without AI, and JSON and Markdown reports |
| M4 | Planned | Model layer, budget ledger, and cache, tested with fake models |
| M5 | Planned | Deterministic triage and the AI judge with its model loop |
| M6 | Planned | Frontend-ready HTTP API and the fixture regression suite |
| M7 | Planned | Remote MCP ingestion over HTTP, hardening, and deployment |

### Completed

- M0: uv project with pinned dependencies, ruff, and pytest; the empty package structure; a settings module with optional provider keys and offline tests; five manual spike scripts and their findings (spikes/FINDINGS.md), which settled the judge output mode, removed the classifier stage, and shaped the quota design.
- M1: typed domain models (tool, finding, evidence, judge verdict, scan report); manifest ingestion that accepts a bare tool array, an MCP tools/list result, or a full JSON-RPC response, reports all problems at once, enforces size and tool-count limits, and computes a content hash; a first fixture corpus of 2 benign and 6 malicious manifests.
- CI: a GitHub Actions workflow runs ruff and pytest on Python 3.11 and 3.12 for every pull request to main and every push to main.

### Work in progress

- Nothing is in progress. M2 (the deterministic rule engine) is the next milestone.

### Later stages

- M2 to M7 as listed in the table.
- After the first version: change detection between scans, sandboxed scanning of local servers, a GitHub Action that calls the API, and streaming scan progress.

## Tech stack

- Python 3.11 or newer (the project pins 3.12), managed with uv
- FastAPI for the HTTP API (planned, M6)
- Pydantic AI for the judge and model access, pydantic-settings for configuration
- Official MCP Python SDK for remote ingestion (planned, M7)
- SQLite for storage (planned, M3)
- pytest and ruff
- Model providers: Groq free tier (primary) and OpenRouter free models (fallback)

## Getting started (development)

There is no API or scan command yet. These steps set up the project and run the tests.

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
    core/               models.py (domain models), errors.py (error codes)
    ingest/             manifest.py (manifest ingestion and content hashing)
    api/  llm/  reporting/  rules/  storage/
  tests/
    unit/               settings, models, manifest, and fixture tests
    fixtures/
      benign/           known-good manifests
      malicious/        manifests with expected findings
  spikes/               M0 verification scripts and FINDINGS.md
```

The api, llm, reporting, rules, and storage packages are still empty. They are filled in by milestones M2 to M7.

## Documentation

- prd.md: what to build and why, scope, requirements, and milestones.
- architecture.md: system shape, components, data flow, and key decisions.
- sdd.md: detailed design, including models, ingestion, the rule catalog, quota handling, the API, and test design.
- pdd.md: product experience, report layout, severity meanings, and message wording.
- instruction.md: working rules and the milestone plan for development.
- spikes/FINDINGS.md: results of the M0 provider spikes and the decisions they led to.

## Fixtures and regression metrics

tests/fixtures/ holds JSON manifests with the expected verdict and findings for each. From M6, a pytest regression suite will run the full pipeline over them and report two numbers: the detection rate on malicious/ fixtures, and the false positive rate, measured on benign/ only. See tests/fixtures/README.md for the format and the exact definitions.

## Free tier note

ToolGuard is designed to run on free model quotas only. As read from provider documentation in October 2026, Groq's free tier allows about 1,000 requests and 200,000 tokens per day for the judge model (the request quota refills continuously), and OpenRouter's free models allow 50 requests per day without purchased credits. These limits change. When AI quota is unavailable or keys are missing, scans degrade to rules-only results and the report says so.

## Security

- Scanned content (tool names, descriptions, schemas) is treated as untrusted data. Ingestion only parses it as JSON text, and error messages never repeat it. It is never executed or followed as instructions, and reports will neutralize it before display (planned, M3).
- No keys or secrets are stored in this repository. Keys come from environment variables only.
- The malicious fixtures are hostile test data with fake values only; see tests/fixtures/README.md.

To report a security issue: CONTACT DETAILS TO BE ADDED.

## License

LICENSE TO BE DECIDED. No license has been chosen yet.
