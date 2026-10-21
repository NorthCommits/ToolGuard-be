"""Spike 4: what the providers report for usage and rate limits, and hints about daily resets.

Real calls: 1 Groq chat completion (max 5 output tokens), 1 OpenRouter chat completion
(max 5 output tokens), and 2 reads of OpenRouter's GET /api/v1/key, before and after, to see
whether the free daily counter moves. The key endpoint is not documented as consuming quota.
Only rate limit headers and selected usage fields are printed, never request headers or keys.
"""

import httpx
from _common import confirm, load_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
KEY_FIELDS = ("limit", "limit_remaining", "limit_reset", "usage", "is_free_tier", "rate_limit")


def print_limit_headers(response: httpx.Response) -> None:
    for name, value in response.headers.items():
        if "ratelimit" in name.lower() or name.lower() == "retry-after":
            print(f"    {name}: {value}")


def print_key_info(client: httpx.Client, headers: dict[str, str], label: str) -> None:
    data = client.get(OPENROUTER_KEY_URL, headers=headers).json().get("data", {})
    selected = {k: data[k] for k in KEY_FIELDS if k in data}
    selected.update({k: v for k, v in data.items() if "free" in k})
    print(f"[openrouter key info, {label}] {selected}")


def chat(client: httpx.Client, url: str, headers: dict[str, str], model: str, label: str) -> None:
    body = {"model": model, "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 5}
    response = client.post(url, headers=headers, json=body)
    print(f"[{label}] status={response.status_code} model={model}")
    print_limit_headers(response)
    if response.is_success:
        print(f"    usage: {response.json().get('usage')}")
    else:
        print(f"    error body: {response.text[:500]}")


def main() -> None:
    settings = load_settings(["groq", "openrouter"])
    confirm(
        groq_calls=1, openrouter_calls=1, note="Plus 2 reads of OpenRouter's key info endpoint."
    )

    groq_headers = {"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"}
    openrouter_headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}"
    }
    with httpx.Client(timeout=30) as client:
        chat(client, GROQ_URL, groq_headers, settings.primary_model, "groq")
        print_key_info(client, openrouter_headers, "before")
        chat(client, OPENROUTER_URL, openrouter_headers, settings.fallback_models[0], "openrouter")
        print_key_info(client, openrouter_headers, "after")


if __name__ == "__main__":
    main()
