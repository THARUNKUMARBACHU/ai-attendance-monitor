"""Check that the configured LLM provider key works.

Supports OpenRouter (default) and OpenAI; both speak the OpenAI-compatible API.
Settings come from environment variables, falling back to the project's .env
file. The key is never printed in full. Uses only the standard library.

Usage:
    python scripts/check_llm_key.py                     # provider from $LLM_PROVIDER, default openrouter
    python scripts/check_llm_key.py --provider openai
    python scripts/check_llm_key.py --model openai/gpt-4.1-nano

Environment (per provider, e.g. OPENROUTER_*): <PROVIDER>_API_KEY (required),
<PROVIDER>_MODEL and <PROVIDER>_BASE_URL (optional).

Exit codes: 0 = key works, 1 = check failed, 2 = key not configured.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 30
# Keep the test call tiny: one short prompt and a handful of output tokens.
MAX_OUTPUT_TOKENS = 16

PROVIDERS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "max_tokens_field": "max_tokens",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "max_tokens_field": "max_completion_tokens",
    },
}


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding variables already set."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def mask(secret: str) -> str:
    return f"{secret[:8]}...{secret[-4:]}" if len(secret) > 16 else "*" * len(secret)


def money(value: object) -> str:
    return f"${value:.4f}" if isinstance(value, (int, float)) else "n/a"


def call_api(method: str, url: str, api_key: str, payload: dict | None = None) -> tuple[int, dict]:
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"Only http(s) endpoints are allowed, got: {url}")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - scheme checked above
    request.add_header("Authorization", f"Bearer {api_key}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"error": {"message": body[:500]}}


def explain_error(status: int, body: dict) -> str:
    error = body.get("error") or {}
    code = error.get("code")
    code_text = f" {code}" if isinstance(code, str) else ""
    hints = {
        400: "bad request (often a parameter this model does not support).",
        401: "the key is invalid, revoked, or mistyped.",
        402: "the key works, but the account has no credits for this model. "
        "Add credits or use a ':free' model.",
        403: "the key is valid but not allowed here (permissions, region, or moderation).",
        404: "the model does not exist or this key cannot access it.",
        429: "rate limited, or the account is out of credit.",
    }
    hint = hints.get(status, "unexpected error from the API.")
    if code in ("insufficient_quota", "credit_balance_exhausted"):
        hint = (
            "the key works, but the account has no credit left. Add credits on the provider's billing page."
        )
    return f"HTTP {status}{code_text} - {hint}\n       API says: {error.get('message', '')}"


def price_note(model_info: dict) -> str:
    """OpenRouter lists per-token USD prices; show them per 1M tokens."""
    pricing = model_info.get("pricing") or {}
    try:
        prompt = float(pricing["prompt"]) * 1_000_000
        completion = float(pricing["completion"]) * 1_000_000
    except (KeyError, TypeError, ValueError):
        return ""
    return f" (${prompt:.2f} in / ${completion:.2f} out per 1M tokens)"


def check_openrouter_key(base_url: str, api_key: str) -> bool:
    """OpenRouter's model list is public, so validate the key via its key-info endpoint."""
    status, body = call_api("GET", f"{base_url}/key", api_key)
    if status == 404:  # older path for the same endpoint
        status, body = call_api("GET", f"{base_url}/auth/key", api_key)
    if status != 200:
        print(f"[FAIL] Authentication: {explain_error(status, body)}")
        return False
    info = body.get("data") or {}
    limit = info.get("limit")
    print(
        "[ OK ] Authentication: key accepted"
        f" (used so far {money(info.get('usage'))},"
        f" spend limit {'none' if limit is None else money(limit)},"
        f" remaining {money(info.get('limit_remaining')) if limit is not None else 'n/a'},"
        f" free tier: {info.get('is_free_tier')})"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that the configured LLM provider key works.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), help="default: $LLM_PROVIDER or openrouter")
    parser.add_argument("--model", help="model to test (default: $<PROVIDER>_MODEL or the provider default)")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    provider = args.provider or os.environ.get("LLM_PROVIDER", "").strip().lower() or "openrouter"
    if provider not in PROVIDERS:
        print(f"[FAIL] Unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(sorted(PROVIDERS))}.")
        return 2
    config = PROVIDERS[provider]
    prefix = provider.upper()
    api_key = os.environ.get(f"{prefix}_API_KEY", "").strip()
    base_url = (os.environ.get(f"{prefix}_BASE_URL", "").strip() or config["base_url"]).rstrip("/")
    model = args.model or os.environ.get(f"{prefix}_MODEL", "").strip() or config["default_model"]

    if not api_key:
        print(
            f"[FAIL] {prefix}_API_KEY is not set. Put it in .env (see .env.example) or set it in your shell."
        )
        return 2

    print(f"Provider : {provider} ({base_url})")
    print(f"Key      : {mask(api_key)}")

    try:
        # Step 1 - authentication (free).
        if provider == "openrouter" and not check_openrouter_key(base_url, api_key):
            return 1
        status, body = call_api("GET", f"{base_url}/models", api_key)
        if status != 200:
            print(f"[FAIL] Model list: {explain_error(status, body)}")
            return 1
        models = {m["id"]: m for m in body.get("data", [])}
        if provider == "openai":
            print(f"[ OK ] Authentication: key accepted, {len(models)} models visible")

        # Step 2 - the model exists (and, on OpenRouter, what it costs).
        if model in models:
            print(f"[ OK ] Model: {model}{price_note(models[model])}")
        else:
            print(f"[WARN] Model: {model} is not in the provider's model list; trying it anyway.")

        # Step 3 - a tiny completion proves credit and model access.
        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
            config["max_tokens_field"]: MAX_OUTPUT_TOKENS,
        }
        if provider == "openrouter":
            payload["usage"] = {"include": True}  # ask OpenRouter to report this call's cost
        status, body = call_api("POST", f"{base_url}/chat/completions", api_key, payload)
        if status != 200:
            print(f"[FAIL] Completion with {model}: {explain_error(status, body)}")
            return 1
        choice = (body.get("choices") or [{}])[0]
        reply = ((choice.get("message") or {}).get("content") or "").strip()
        usage = body.get("usage") or {}
        cost = usage.get("cost")
        cost_note = f", cost ${cost:.6f}" if isinstance(cost, (int, float)) else ""
        tokens = usage.get("total_tokens", "?")
        print(f"[ OK ] Completion: {model} replied {reply!r} ({tokens} tokens{cost_note})")
    except urllib.error.URLError as exc:
        print(f"[FAIL] Could not reach {base_url}: {exc.reason}. Check your network or proxy.")
        return 1

    print("Result   : the key works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
