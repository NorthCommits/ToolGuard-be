"""Spike 2: how the Groq prompt-injection classifier is invoked and what it returns.

The Groq docs show a plain chat completion call and do not document the response format, and
the model is in the preview tier with a 512 token context window. This spike prints the raw
content so we can see whether it is a score, a label, or something else.

Real calls: 3 Groq calls (benign text, injection text, text well over 512 tokens).
SDK-level retries are disabled.
"""

from _common import CLASSIFIER_MODEL, SAMPLE_TOOLS, confirm, load_settings
from groq import Groq

TEXTS = {
    "benign": SAMPLE_TOOLS["benign"]["description"],
    "injection": SAMPLE_TOOLS["malicious"]["description"],
    "over_limit": "Returns the weather for a city. " * 150,  # roughly 1,000 tokens
}


def main() -> None:
    settings = load_settings(["groq"])
    print(f"classifier_model: {CLASSIFIER_MODEL}")
    confirm(groq_calls=3, openrouter_calls=0)

    client = Groq(api_key=settings.groq_api_key.get_secret_value(), max_retries=0)
    for name, text in TEXTS.items():
        try:
            completion = client.chat.completions.create(
                model=CLASSIFIER_MODEL,
                messages=[{"role": "user", "content": text}],
            )
        except Exception as exc:  # spike: report every failure type as is
            print(f"[{name}] FAILED: {type(exc).__name__}: {exc}")
            continue
        print(f"[{name}] chars={len(text)} content={completion.choices[0].message.content!r}")
        print(f"    usage: {completion.usage}")


if __name__ == "__main__":
    main()
