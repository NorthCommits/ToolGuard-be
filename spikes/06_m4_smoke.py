"""Spike 6: smoke test of the M4 model layer against the real providers. Run manually only.

Real HTTP requests, 4 in total:
1. OpenRouter GET /api/v1/models (public, no key, no quota): the reasoning efforts each
   fallback model supports, and whether its exact id is listed.
2. OpenRouter GET /api/v1/key (no completion quota): only the used, limit, and remaining numbers
   of the free-model daily counter.
3. One Groq chat completion to the primary model through run_attempt: checks max_retries 0,
   reasoning effort low, and that the quota headers reach the ledger.
4. One OpenRouter chat completion to the first fallback model through run_attempt: checks the
   httpx2 client and the configured reasoning effort. Uses 1 of the free daily requests.

The ledger uses a temporary database, so toolguard.db is untouched. Key values and account,
user, or workspace identifiers are never printed.

Run from the repository root: uv run python spikes/06_m4_smoke.py

With --key-info-only it makes 1 real request, step 2 alone (no completion quota), for example a
few minutes after a full run, to see whether the free daily counter has moved.
"""

import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx2
from _common import confirm, load_settings
from pydantic import BaseModel
from pydantic_ai import Agent

from toolguard.llm.budget import Ledger
from toolguard.llm.provider import build_chain, read_openrouter_key_info, run_attempt

MODELS_URL = "https://openrouter.ai/api/v1/models"
MAX_OUTPUT_TOKENS = 400  # generous: reasoning counts as output (spike 04)
ESTIMATED_TOKENS = 1_000


class Answer(BaseModel):
    word: str


AGENT = Agent(
    output_type=Answer,
    retries=0,  # no validation retry inside an attempt, as the judge will run
    instructions="Answer with the single word ok.",
)


def find_efforts(node, path=""):
    """Every key that mentions effort, anywhere in a model entry."""
    if isinstance(node, dict):
        for key, value in node.items():
            if "effort" in key.lower():
                yield f"{path}{key}", value
            yield from find_efforts(value, f"{path}{key}.")
    elif isinstance(node, list):
        for item in node:
            yield from find_efforts(item, path)


async def list_supported_efforts(models: list[str]) -> None:
    async with httpx2.AsyncClient(timeout=30) as client:
        response = await client.get(MODELS_URL)  # no Authorization header
    print(f"[1 models list] status={response.status_code}")
    if response.status_code != 200:
        return
    entries = {entry.get("id"): entry for entry in response.json().get("data", [])}
    for name in models:
        entry = entries.get(name)
        if entry is None:
            print(f"    {name}: not listed")
            continue
        efforts = dict(find_efforts(entry)) or "no effort field"
        print(f"    {name}: listed, efforts {efforts}")


async def key_info_only() -> None:
    settings = load_settings(["openrouter"])
    print("Plan:")
    print("  OpenRouter GET /api/v1/key (no completion quota)")
    confirm(0, 1, "No completion is made.")
    with tempfile.TemporaryDirectory() as folder:
        settings = settings.model_copy(update={"database_path": Path(folder) / "smoke.db"})
        info = await read_openrouter_key_info(Ledger(settings))
    if info is None:
        print("[key info] unavailable")
    else:
        print(f"[key info] used={info.used} limit={info.limit} remaining={info.remaining}")


async def main() -> None:
    settings = load_settings(["groq", "openrouter"])
    fallback = settings.fallback_models[0]
    print("Plan:")
    print("  1. OpenRouter GET /api/v1/models (public, no key, no quota)")
    print("  2. OpenRouter GET /api/v1/key (no completion quota)")
    print(f"  3. Groq completion to {settings.primary_model} (max {MAX_OUTPUT_TOKENS} tokens)")
    print(f"  4. OpenRouter completion to {fallback} (max {MAX_OUTPUT_TOKENS} tokens)")
    confirm(1, 3, "OpenRouter: 3 requests, of which 1 completion counts against the free daily 50.")

    logging.basicConfig(level=logging.INFO, format="    log: %(message)s")
    # Request lines with full URLs are not needed (sdd.md section 20).
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
    with tempfile.TemporaryDirectory() as folder:
        database = Path(folder) / "smoke.db"
        settings = settings.model_copy(update={"database_path": database})
        ledger = Ledger(settings)

        await list_supported_efforts(settings.fallback_models)

        info = await read_openrouter_key_info(ledger)
        if info is None:
            print("[2 key info] unavailable")
        else:
            print(f"[2 key info] used={info.used} limit={info.limit} remaining={info.remaining}")

        async def call(spec):
            run_settings = {**spec.settings, "max_tokens": MAX_OUTPUT_TOKENS}
            return await AGENT.run("Reply.", model=spec.model, model_settings=run_settings)

        chain = build_chain(settings)
        groq = chain[0]
        openrouter = next(spec for spec in chain if spec.name == fallback)
        for number, spec in ((3, groq), (4, openrouter)):
            result = await run_attempt(ledger, spec, ESTIMATED_TOKENS, call)
            print(f"[{number} {spec.provider}] {type(result).__name__}: {result}")

        with sqlite3.connect(database) as conn:
            conn.row_factory = sqlite3.Row
            print("[ledger] calls:")
            for row in conn.execute(
                "SELECT provider, model, status, outcome, requests, estimated_tokens, "
                "input_tokens, output_tokens FROM ai_calls ORDER BY id"
            ):
                print(f"    {dict(row)}")
            print("[ledger] provider quota:")
            for row in conn.execute("SELECT * FROM provider_quota ORDER BY provider"):
                print(f"    {dict(row)}")


if __name__ == "__main__":
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    asyncio.run(key_info_only() if "--key-info-only" in sys.argv[1:] else main())
