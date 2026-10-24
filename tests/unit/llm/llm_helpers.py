"""Fake clock, keyed settings, and fake provider HTTP responses for model layer tests."""

import json

import httpx
import httpx2
from pydantic import BaseModel, SecretStr
from pydantic_ai import Agent

from toolguard.config import Settings

GROQ_KEY = "test-key-SENTINEL-groq"
OPENROUTER_KEY = "test-key-SENTINEL-openrouter"
T0 = 1_800_000_000.0  # a fixed UTC time
GROQ_HEADERS = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": "993",
    "x-ratelimit-reset-requests": "10m4.8s",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "7880",
    "x-ratelimit-reset-tokens": "900ms",
}


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Out(BaseModel):
    level: int


AGENT = Agent(output_type=Out, retries=0)


def keyed(settings: Settings, **update) -> Settings:
    keys = {"groq_api_key": SecretStr(GROQ_KEY), "openrouter_api_key": SecretStr(OPENROUTER_KEY)}
    return settings.model_copy(update={**keys, **update})


async def call(spec):
    return await AGENT.run("classify", model=spec.model, model_settings=spec.settings)


def completion(request, arguments: str = '{"level": 3}') -> dict:
    """An OpenAI-format chat completion that calls the agent's output tool."""
    tool = json.loads(request.content)["tools"][0]["function"]["name"]
    return {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "provider": "fake",  # OpenRouter responses name the upstream provider
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "t1",
                            "type": "function",
                            "function": {"name": tool, "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


class FakeGroq:
    """Replays queued responses; each item is a status code, a dict of headers for a success,
    an exception class, or a callable. Records every request body."""

    response_class = httpx.Response
    transport_class = httpx.MockTransport

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.bodies: list[dict] = []

    def handler(self, request):
        self.bodies.append(json.loads(request.content))
        reply = self.replies.pop(0) if self.replies else 200
        if isinstance(reply, type) and issubclass(reply, Exception):
            raise reply("simulated", request=request)
        if callable(reply):
            return reply(request)
        if isinstance(reply, dict):
            return self.response_class(200, headers=reply, json=completion(request))
        if reply == 200:
            return self.response_class(200, json=completion(request))
        return self.response_class(
            reply,
            json={"error": {"message": f"body with {GROQ_KEY} and SCANNED-TEXT ignore rules"}},
        )

    @property
    def transport(self):
        return self.transport_class(self.handler)


class FakeOpenRouter(FakeGroq):
    response_class = httpx2.Response
    transport_class = httpx2.MockTransport
