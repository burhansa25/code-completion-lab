"""Single OpenAI-compatible HTTP adapter for OpenAI, xAI, and Groq."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODELS = {
    "grok": {"id": "grok-4.20-0309-non-reasoning", "provider": "xai", "key_env": "XAI_API_KEY", "url": "https://api.x.ai/v1"},
    "gpt4o": {"id": "gpt-4o", "provider": "openai", "key_env": "OPENAI_API_KEY", "url": "https://api.openai.com/v1"},
    "gptoss": {"id": "openai/gpt-oss-120b", "provider": "groq", "key_env": "GROQ_API_KEY", "url": "https://api.groq.com/openai/v1"},
}
ALIASES = {"gpt": "gpt4o"}


def resolve_model(name):
    name = ALIASES.get(name, name)
    if name not in MODELS:
        raise ValueError(f"Unknown model '{name}', choose {', '.join(MODELS)} (gpt is alias for gpt4o)")
    return name, dict(MODELS[name])


def payload_for(model, prompt, temperature, max_tokens):
    body = {"model": model["id"], "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature}
    # Preserve provider-specific parameter behavior of the original runners.
    token_key = "max_tokens" if model["provider"] == "xai" else "max_completion_tokens"
    body[token_key] = max_tokens
    if model["provider"] == "groq" and model["id"].startswith("openai/gpt-oss"):
        body.update(reasoning_effort="low", include_reasoning=False)
    return body


def call_api(model, payload, *, timeout=120):
    key = os.environ.get(model["key_env"], "").strip()
    if not key:
        raise ValueError(f"Missing {model['key_env']}")
    request = Request(model["url"] + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json", "User-Agent": "CodeCompletionLab-v2/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        msg = exc.read().decode("utf-8", "replace")[:2000].replace(key, "[REDACTED]")
        raise RuntimeError(f"{model['provider']} HTTP {exc.code}: {msg}") from None
    except URLError as exc:
        raise RuntimeError(f"{model['provider']} network error: {str(exc.reason).replace(key, '[REDACTED]')}") from None
