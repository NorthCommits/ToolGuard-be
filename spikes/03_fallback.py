"""Spike 3: does FallbackModel move on after a rate limit, and what happens on invalid output.

Part A (offline): a fake model raises HTTP 429, a second fake model answers. Expect fallback.
Part B (offline): a fake model always returns invalid output. Expect no fallback; the agent
retries output validation and then raises.
Part C (real): the fake 429 model, then the first configured OpenRouter free model.
Real calls: 1 OpenRouter call, 2 if an output validation retry fires. No Groq calls.
"""

import asyncio
import json

from _common import (
    JUDGE_INSTRUCTIONS,
    SAMPLE_TOOLS,
    SpikeVerdict,
    confirm,
    load_settings,
    tool_prompt,
)
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

PROMPT = tool_prompt(SAMPLE_TOOLS["malicious"])


def rate_limited(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise ModelHTTPError(status_code=429, model_name="fake-groq", body={"error": "rate limit"})


def valid_answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    args = {
        "tool_name": "add_numbers",
        "risk_level": "critical",
        "suspicious_phrases": ["read ~/.ssh/id_rsa"],
        "reasoning_summary": "fake answer",
    }
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, json.dumps(args))])


def invalid_answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, '{"tool_name": 1}')])


async def run(label: str, agent: Agent) -> None:
    try:
        result = await agent.run(PROMPT)
    except Exception as exc:  # spike: report every failure type as is
        print(f"[{label}] raised {type(exc).__name__}: {exc}")
        for sub in getattr(exc, "exceptions", []):
            print(f"    inner: {type(sub).__name__}: {sub}")
        return
    response_models = [m.model_name for m in result.all_messages() if isinstance(m, ModelResponse)]
    print(f"[{label}] ok: risk_level={result.output.risk_level} answered_by={response_models}")
    print(f"    usage: requests={result.usage.requests}")


async def main() -> None:
    print("Part A, offline: 429 then a valid fake model")
    fallback = FallbackModel(FunctionModel(rate_limited), FunctionModel(valid_answer))
    await run("A", Agent(fallback, output_type=SpikeVerdict, instructions=JUDGE_INSTRUCTIONS))

    print("Part B, offline: invalid output, with a valid model behind it")
    fallback = FallbackModel(FunctionModel(invalid_answer), FunctionModel(valid_answer))
    agent = Agent(
        fallback, output_type=SpikeVerdict, instructions=JUDGE_INSTRUCTIONS, retries={"output": 1}
    )
    await run("B", agent)

    print("Part C, real: 429 fake model then OpenRouter")
    settings = load_settings(["openrouter"])
    print(f"OpenRouter model: {settings.fallback_models[0]}")
    confirm(groq_calls=0, openrouter_calls=2, note="Expected 1; 2 if a validation retry fires.")
    openrouter = OpenRouterModel(
        settings.fallback_models[0],
        provider=OpenRouterProvider(api_key=settings.openrouter_api_key.get_secret_value()),
        settings=ModelSettings(temperature=0, max_tokens=1000, max_retries=0),
    )
    fallback = FallbackModel(FunctionModel(rate_limited), openrouter)
    agent = Agent(
        fallback, output_type=SpikeVerdict, instructions=JUDGE_INSTRUCTIONS, retries={"output": 1}
    )
    await run("C", agent)


if __name__ == "__main__":
    asyncio.run(main())
