"""
Unified LLM provider chain — the single place that knows about Local
(Ollama/vLLM), Gemini, and Groq. Every call site in the codebase
(explanation, care plan, evidence overview, evidence cards, fact-check
judge, guideline cache synthesis) goes through call_llm() here instead of
each hand-rolling its own try-Gemini-then-Groq block.

Why this module exists: before adding a local LLM option, the
Gemini-then-Groq fallback was duplicated across 6 call sites in
gemini_service.py and fact_check.py. Adding a third provider by copy-pasting
into all 6 would mean any future provider change (retry tuning, a fourth
provider, HIPAA-strict lockout) needs 6 edits instead of 1. This collapses
that to one chain, built once from config.py settings.

Provider order (each optional, skipped if disabled/unconfigured):
  1. Local  — Ollama or vLLM via an OpenAI-compatible /v1/chat/completions
              endpoint. No data leaves your infrastructure. Free, no
              per-token cost, no external API dependency at all.
  2. Gemini — cloud, free tier. Skipped entirely if
              settings.disable_cloud_llm_fallback is True.
  3. Groq   — cloud, free tier. Same skip condition as Gemini.

If every enabled provider fails, raises LLMUnavailableError — callers
already have their own rule-based fallback for that case (see
services/gemini_service.py's _fallback_explanation / _fallback_care_plan).
"""

from __future__ import annotations

import json
import threading
from typing import Callable

import google.generativeai as genai
import httpx
from groq import Groq

DEFAULT_SYSTEM_PROMPT = (
    "You are NeoGuard AI, a neonatal clinical decision support engine. "
    "You always respond with ONLY a single valid JSON object — no markdown "
    "fences, no preamble, no text before or after the JSON."
)


class LLMUnavailableError(Exception):
    """Raised when every enabled provider in the chain failed."""


# Ollama (and any other CPU-served single-instance local model) can only
# run one generation at a time regardless of how many requests the backend
# sends it — extra requests queue internally on Ollama's side. Without this
# lock, FastAPI's thread pool happily fires multiple concurrent requests at
# Ollama, and each one's own httpx timeout clock starts ticking the moment
# IT is sent, not when Ollama actually starts working on it. A request
# queued 2nd or 3rd can burn its entire timeout budget just waiting in
# Ollama's internal queue and fail even though nothing was actually broken.
#
# This lock makes the backend serialize Local LLM calls itself, so a
# request's httpx timeout only starts counting once it has actually
# acquired exclusive access and is about to be sent — giving every request
# its full configured timeout budget for the generation itself, not for
# generation-plus-queueing. acquire(timeout=...) below means a request that
# can't get a turn within its own timeout budget fails fast and falls
# through to Gemini/Groq, instead of queuing indefinitely behind an
# unbounded backlog.
_local_llm_lock = threading.Lock()


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text


def _validate_json(raw: str) -> None:
    """Raises if raw (after fence-stripping) isn't valid JSON. Callers do
    their own final parsing/fence-stripping — this is just the provider
    chain's own retry trigger, so a malformed response from one attempt
    retries rather than silently returning garbage."""
    json.loads(_strip_json_fences(raw))


# ── Provider implementations ────────────────────────────────────────────────

def _call_local(prompt: str, settings, system: str, require_json: bool) -> str:
    """
    Calls a local Mistral-7B (or any other model) served via Ollama or vLLM
    through their shared OpenAI-compatible /v1/chat/completions endpoint —
    this is deliberately the ONLY interface this function depends on, so
    switching from Ollama (dev) to vLLM (production) is a config change
    (LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL in .env), never a code change.
    """
    payload = {
        "model": settings.local_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
        # Keep the model resident in memory between requests. Ollama's
        # default keep_alive is 5 minutes — without this, any gap longer
        # than that forces a full cold reload on the next request.
        "keep_alive": settings.local_llm_keep_alive,
    }
    # response_format:"json_object" makes Ollama use grammar-constrained
    # token decoding (every candidate token checked against a JSON grammar
    # before being accepted) — on CPU this is a severe per-token slowdown,
    # commonly 5-10x, and is the most likely explanation for "interactive
    # `ollama run` chat feels normal, but the API call with the full
    # 7-section schema prompt never finishes inside a multi-minute
    # timeout." Gated behind local_llm_json_mode, default OFF: instead we
    # rely on the strong system-prompt instruction above plus this module's
    # automatic retry-on-parse-failure (_validate_json below) — a much
    # cheaper way to get reliably-parseable JSON out of a CPU-served 7B
    # model than paying the grammar-decoding tax on every single token.
    # Set LOCAL_LLM_JSON_MODE=true once you're on a GPU (where the
    # per-token overhead is negligible) if you want the stronger guarantee.
    if require_json and settings.local_llm_json_mode:
        payload["response_format"] = {"type": "json_object"}
    print(f"[LocalLLM] Waiting for local-LLM lock (model={settings.local_llm_model})")
    acquired = _local_llm_lock.acquire(timeout=settings.local_llm_timeout_seconds)
    if not acquired:
        raise TimeoutError(
            f"Timed out after {settings.local_llm_timeout_seconds}s waiting for a turn "
            f"on the local LLM — too many concurrent requests queued ahead of this one."
        )
    try:
        print(f"[LocalLLM] Acquired lock — calling model={settings.local_llm_model} "
              f"@ {settings.local_llm_base_url} (json_mode={settings.local_llm_json_mode})")
        with httpx.Client(timeout=settings.local_llm_timeout_seconds) as client:
            response = client.post(f"{settings.local_llm_base_url}/chat/completions", json=payload)
            if response.status_code >= 400:
                # raise_for_status() alone discards the response body — for
                # a local llama.cpp/Ollama server that body almost always
                # contains the actual reason (most commonly: prompt token
                # count exceeds the model's loaded context window, which
                # this codebase doesn't currently guard against — see the
                # num_ctx note in config.py's local_llm_model docstring).
                # Logging it here turns "500 Internal Server Error" into an
                # actionable message instead of a dead end.
                print(f"[LocalLLM] Ollama returned {response.status_code}: {response.text[:2000]}")
            response.raise_for_status()
            data = response.json()
    finally:
        _local_llm_lock.release()
    return data["choices"][0]["message"]["content"]


def _call_gemini(prompt: str, settings, system: str, require_json: bool) -> str:
    generation_config = {
        "temperature": 0.1,
        "top_p": 0.9,
        "max_output_tokens": 2048,
    }
    if require_json:
        generation_config["response_mime_type"] = "application/json"
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=system,
        generation_config=generation_config,
    )
    print(f"[Gemini] Calling model={settings.gemini_model}")
    response = model.generate_content(prompt)
    return response.text


def _call_groq(prompt: str, settings, system: str, require_json: bool) -> str:
    client = Groq(api_key=settings.groq_api_key)
    print(f"[Groq] Calling model={settings.groq_model}")
    kwargs: dict = {}
    if require_json:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=2048,
        **kwargs,
    )
    return response.choices[0].message.content


# ── Chain builder ────────────────────────────────────────────────────────────

def _build_chain(settings) -> list[tuple[str, Callable, str]]:
    """Returns [(provider_name, call_fn, model_version), ...] in try-order."""
    chain: list[tuple[str, Callable, str]] = []

    if settings.use_local_llm and settings.local_llm_enabled:
        chain.append(("Local", _call_local, settings.local_llm_model))

    if not settings.use_local_llm or not settings.disable_cloud_llm_fallback:
        if settings.gemini_api_key:
            chain.append(("Gemini", _call_gemini, settings.gemini_model))
        if settings.groq_api_key:
            chain.append(("Groq", _call_groq, settings.groq_model))

    return chain


# ── Public entry point ────────────────────────────────────────────────────────

def call_llm(
    prompt: str,
    settings=None,
    *,
    system: str = DEFAULT_SYSTEM_PROMPT,
    require_json: bool = True,
    retries_per_provider: int = 1,
) -> tuple[str, str]:
    """
    Tries each enabled provider in order, retrying each up to
    retries_per_provider extra times on a JSON-parse failure (local 7B
    models are meaningfully less reliable at strict JSON than Gemini/GPT-
    class models when not using grammar-constrained decoding — see
    local_llm_json_mode's docstring in config.py for the tradeoff).

    Returns (raw_response_text, model_version_used) — model_version_used is
    what gets stored in llm_explanations.gemini_model_version and shown in
    the UI's "AI-generated · <model> · <guideline>" label, so it's real
    even when the provider isn't actually Gemini.

    Raises LLMUnavailableError if every enabled provider is exhausted.
    Callers should catch this and fall through to their own rule-based
    fallback (see gemini_service.py's _fallback_explanation/_fallback_care_plan).
    """
    from config import get_settings
    settings = settings or get_settings()
    chain = _build_chain(settings)

    if not chain:
        raise LLMUnavailableError(
            "No LLM provider is enabled/configured. Set USE_LOCAL_LLM=true with "
            "LOCAL_LLM_ENABLED=true (and run Ollama/vLLM), or set USE_LOCAL_LLM=false "
            "with GEMINI_API_KEY/GROQ_API_KEY configured."
        )

    last_error: Exception | None = None
    for name, call_fn, model_version in chain:
        for attempt in range(retries_per_provider + 1):
            try:
                raw = call_fn(prompt, settings, system, require_json)
                if require_json:
                    _validate_json(raw)
                print(f"[LLMProvider] {name} succeeded (attempt {attempt + 1})")
                return raw, model_version
            except (json.JSONDecodeError, ValueError) as e:
                # Malformed JSON is often genuinely transient with a smaller/
                # local model — worth one same-provider retry.
                last_error = e
                print(f"[LLMProvider] {name} attempt {attempt + 1}/{retries_per_provider + 1} "
                      f"produced invalid JSON — retrying same provider")
                continue
            except Exception as e:
                # Timeouts, connection errors, and quota/rate-limit errors
                # will almost always fail identically on an immediate retry
                # — burning a full second timeout window here is exactly
                # what turned a single slow/misconfigured provider into a
                # multi-minute wait before ever reaching a working one.
                # Move to the next provider immediately instead.
                last_error = e
                print(f"[LLMProvider] {name} failed ({type(e).__name__}: {e}) — "
                      f"moving to next provider without retry")
                break

    raise LLMUnavailableError(f"All LLM providers exhausted. Last error: {last_error}")