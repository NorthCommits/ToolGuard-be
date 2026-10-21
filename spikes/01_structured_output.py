"""Spike 1: which Pydantic AI output mode works reliably with the Groq GPT-OSS judge model.

Real calls: 3 output modes x 2 sample tools = 6 Groq calls. One output validation retry is
allowed per run, so the worst case is 12. SDK-level retries are disabled (max_retries=0).
"""

import asyncio

from _common import (
    JUDGE_INSTRUCTIONS,
    SAMPLE_TOOLS,
    SpikeVerdict,
    confirm,
    load_settings,
    tool_prompt,
)
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.models.groq import GroqModel, GroqModelSettings
from pydantic_ai.providers.groq import GroqProvider

MODES = {
    "tool": ToolOutput(SpikeVerdict),
    "native": NativeOutput(SpikeVerdict),
    "prompted": PromptedOutput(SpikeVerdict),
}


async def main() -> None:
    settings = load_settings(["groq"])
    confirm(
        groq_calls=12, openrouter_calls=0, note="Expected 6; up to 12 if validation retries fire."
    )

    model = GroqModel(
        settings.primary_model,
        provider=GroqProvider(api_key=settings.groq_api_key.get_secret_value()),
        settings=GroqModelSettings(
            temperature=0, max_tokens=1000, max_retries=0, groq_reasoning_effort="low"
        ),
    )
    total_requests = 0
    for mode_name, output_type in MODES.items():
        agent = Agent(
            model, output_type=output_type, instructions=JUDGE_INSTRUCTIONS, retries={"output": 1}
        )
        for sample_name, sample in SAMPLE_TOOLS.items():
            label = f"[{mode_name} / {sample_name}]"
            try:
                result = await agent.run(tool_prompt(sample))
            except Exception as exc:  # spike: report every failure type as is
                print(f"{label} FAILED: {type(exc).__name__}: {exc}")
                continue
            usage = result.usage
            total_requests += usage.requests
            print(
                f"{label} ok: risk_level={result.output.risk_level} "
                f"phrases={len(result.output.suspicious_phrases)} requests={usage.requests} "
                f"input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}"
            )
            print(f"    summary: {result.output.reasoning_summary}")
    print(f"Successful-run requests counted: {total_requests}")


if __name__ == "__main__":
    asyncio.run(main())
