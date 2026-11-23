"""AI calls: embeddings, one-shot completions and streamed completions.

`OpenAICompatClient` talks to any OpenAI-compatible API. Two providers are set up:
Google Gemini (default) and OpenRouter. Free tiers have per-minute and per-day
limits, so 429/502/503 answers are retried with backoff (honouring `Retry-After`),
and then surface as `RateLimited` with a plain message.
"""

import json
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Protocol

import httpx

from .config import Settings, get_settings

Message = dict[str, str]


class AIError(Exception):
    """The AI service failed; the message is safe to show to the user."""


class RateLimited(AIError):
    pass


class AIClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def complete(self, messages: list[Message], max_tokens: int = 600) -> str: ...
    def stream(self, messages: list[Message], max_tokens: int = 900) -> Iterator[str]: ...


RATE_LIMIT_MESSAGE = (
    "The free AI model is rate-limited or busy right now. Please wait a minute and try again."
)


PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_name": "GEMINI_API_KEY",
        # Keep "thinking" to a minimum: copying a clause needs no long reasoning.
        "thinking_off": {"reasoning_effort": "low"},
        "embed_dimensions": True,  # Gemini can return shorter vectors (we ask for EMBED_DIM)
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_name": "OPENROUTER_API_KEY",
        "thinking_off": {"reasoning": {"enabled": False}},
        "embed_dimensions": False,
    },
}


class OpenAICompatClient:
    RETRY_STATUSES = {429, 502, 503}

    def __init__(self, settings: Settings, max_tries: int = 4):
        if settings.ai_provider not in PROVIDERS:
            raise AIError(f"AI_PROVIDER must be one of {', '.join(PROVIDERS)}.")
        self.provider = PROVIDERS[settings.ai_provider]
        key = settings.gemini_api_key if settings.ai_provider == "gemini" else settings.openrouter_api_key
        if not key:
            raise AIError(f"{self.provider['key_name']} is not set. Add it to your .env file.")
        self.s = settings
        self.max_tries = max_tries
        self.http = httpx.Client(
            base_url=self.provider["base_url"],
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={"Authorization": f"Bearer {key}"},
        )

    # --- helpers -----------------------------------------------------------
    def _wait(self, attempt: int, response: httpx.Response | None) -> None:
        delay = 2.0 * (2**attempt)
        if response is not None:
            try:
                delay = max(delay, float(response.headers.get("retry-after", 0)))
            except ValueError:
                pass
        time.sleep(min(delay, 30.0))

    def _post(self, path: str, payload: dict) -> dict:
        last: httpx.Response | None = None
        for attempt in range(self.max_tries):
            try:
                r = self.http.post(path, json=payload)
            except httpx.TimeoutException as exc:
                if attempt + 1 == self.max_tries:
                    raise AIError("The AI service took too long to answer. Please try again.") from exc
                self._wait(attempt, None)
                continue
            except httpx.HTTPError as exc:
                raise AIError("Could not reach the AI service. Check your internet connection.") from exc
            if r.status_code in self.RETRY_STATUSES:
                last = r
                _raise_if_daily_limit(r)  # waiting seconds won't help with a per-day limit
                if attempt + 1 < self.max_tries:
                    self._wait(attempt, r)
                    continue
                raise RateLimited(RATE_LIMIT_MESSAGE)
            if r.status_code == 401:
                raise AIError("The OpenRouter API key was rejected. Check OPENROUTER_API_KEY in .env.")
            if r.status_code >= 400:
                raise AIError(f"The AI service returned an error ({r.status_code}): {_error_text(r)}")
            body = r.json()
            if isinstance(body, dict) and body.get("error"):
                err = body["error"]
                code = err.get("code") if isinstance(err, dict) else None
                if code in (429, 502, 503) and attempt + 1 < self.max_tries:
                    self._wait(attempt, None)
                    continue
                if code == 429:
                    raise RateLimited(RATE_LIMIT_MESSAGE)
                raise AIError(f"The AI service returned an error: {err}")
            return body
        raise RateLimited(RATE_LIMIT_MESSAGE) if last is not None else AIError("AI request failed.")

    # --- API ---------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, sending them in batches (not one call per chunk)."""
        vectors: list[list[float]] = []
        size = max(1, self.s.embed_batch_size)
        for i in range(0, len(texts), size):
            batch = texts[i : i + size]
            payload: dict = {"model": self.s.embed_model, "input": batch}
            if self.provider["embed_dimensions"]:
                payload["dimensions"] = self.s.embed_dim
            body = self._post("/embeddings", payload)
            data = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
            if len(data) != len(batch):
                raise AIError("The embedding model returned the wrong number of vectors.")
            vectors.extend(d["embedding"] for d in data)
        return vectors

    def _chat_payload(self, messages: list[Message], max_tokens: int) -> dict:
        return {
            "model": self.s.chat_model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            # Free models "think" before answering. Thinking used up the whole token budget
            # (empty answers) and delayed the first streamed word, so it is turned off/low:
            # the task is copying a clause, not solving a puzzle.
            **self.provider["thinking_off"],
        }

    def complete(self, messages: list[Message], max_tokens: int = 1000) -> str:
        body = self._post("/chat/completions", self._chat_payload(messages, max_tokens))
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("The AI model returned an empty answer.") from exc

    def stream(self, messages: list[Message], max_tokens: int = 900) -> Iterator[str]:
        payload = {**self._chat_payload(messages, max_tokens), "stream": True}
        for attempt in range(self.max_tries):
            with self.http.stream("POST", "/chat/completions", json=payload) as r:
                if r.status_code in self.RETRY_STATUSES:
                    r.read()
                    _raise_if_daily_limit(r)
                    if attempt + 1 < self.max_tries:
                        self._wait(attempt, r)
                        continue
                    raise RateLimited(RATE_LIMIT_MESSAGE)
                if r.status_code >= 400:
                    r.read()
                    if r.status_code == 401:
                        raise AIError("The OpenRouter API key was rejected. Check OPENROUTER_API_KEY in .env.")
                    raise AIError(f"The AI service returned an error ({r.status_code}): {_error_text(r)}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue  # keep-alive comments such as ": OPENROUTER PROCESSING"
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        err = event["error"]
                        if isinstance(err, dict) and err.get("code") == 429:
                            raise RateLimited(RATE_LIMIT_MESSAGE)
                        raise AIError(f"The AI service stopped with an error: {err}")
                    for choice in event.get("choices", []):
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            yield piece
                return


def _raise_if_daily_limit(r: httpx.Response) -> None:
    """Free tiers allow a fixed number of requests per day. Say so plainly, with the reset time if known."""
    if r.status_code != 429 or not any(s in r.text for s in ("per-day", "PerDay", "per day")):
        return
    message = "The free AI model's daily request limit is used up."
    try:
        reset_ms = int(r.headers.get("x-ratelimit-reset") or r.json()["error"]["metadata"]["headers"]["X-RateLimit-Reset"])
        reset = datetime.fromtimestamp(reset_ms / 1000, tz=timezone.utc)
        message += f" It resets at {reset:%H:%M} UTC. Please try again after that."
    except (KeyError, TypeError, ValueError):
        message += " It resets at midnight UTC. Please try again after that."
    raise RateLimited(message)


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        if isinstance(body, list) and body:  # Gemini wraps errors in a list
            body = body[0]
        err = body.get("error", body)
        return str(err.get("message", err)) if isinstance(err, dict) else str(err)
    except Exception:  # noqa: BLE001
        return r.text[:200]




_client: AIClient | None = None


def get_ai() -> AIClient:
    global _client
    if _client is None:
        _client = OpenAICompatClient(get_settings())
    return _client


def set_ai(client: AIClient | None) -> None:
    """Used by tests to swap in a fake."""
    global _client
    _client = client
