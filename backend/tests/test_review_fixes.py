"""Tests for the /review fixes and for the real OpenRouter client (HTTP mocked, no network)."""

import io
import json
import zipfile

import httpx
import pytest

from app import llm
from app.config import Settings
from app.models import Contract
from app.db import new_session
from app.parsing import ParseError, extract_text


def test_retry_while_processing_is_409(client, auth, services_txt):
    cid = client.post("/api/contracts", headers=auth("user_a"), files={"file": ("a.txt", services_txt)}).json()["id"]
    with new_session() as db:
        db.get(Contract, __import__("uuid").UUID(cid)).status = "processing"
        db.commit()
    assert client.post(f"/api/contracts/{cid}/review", headers=auth("user_a")).status_code == 409


def test_nul_characters_are_removed():
    out = extract_text("txt", b"This agreement\x00 is governed by the laws of India.")
    assert "\x00" not in out.full_text


def test_zip_that_is_not_word_gives_clear_error():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not a word file")
    with pytest.raises(ParseError, match="not a Word document"):
        extract_text("docx", buf.getvalue())


# --- OpenRouter client -------------------------------------------------------
def make_client(handler, monkeypatch, **overrides) -> llm.OpenRouterClient:
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    s = Settings(openrouter_api_key="test-key", embed_batch_size=2, **overrides)
    c = llm.OpenRouterClient(s)
    c.http = httpx.Client(base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    return c


def chat_ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_retries_429_then_succeeds(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "1"}) if len(calls) < 3 else chat_ok("hi")

    c = make_client(handler, monkeypatch)
    assert c.complete([{"role": "user", "content": "x"}]) == "hi"
    assert len(calls) == 3


def test_persistent_429_raises_rate_limited(monkeypatch):
    c = make_client(lambda r: httpx.Response(429), monkeypatch)
    with pytest.raises(llm.RateLimited, match="rate-limited"):
        c.complete([{"role": "user", "content": "x"}])


def test_bad_key_gives_plain_message(monkeypatch):
    c = make_client(lambda r: httpx.Response(401, json={"error": {"message": "no"}}), monkeypatch)
    with pytest.raises(llm.AIError, match="API key was rejected"):
        c.complete([{"role": "user", "content": "x"}])


def test_embeddings_go_in_batches(monkeypatch):
    sizes = []

    def handler(request):
        batch = json.loads(request.content)["input"]
        sizes.append(len(batch))
        # return out of order to check we sort by index
        data = [{"index": i, "embedding": [float(i)]} for i in reversed(range(len(batch)))]
        return httpx.Response(200, json={"data": data})

    c = make_client(handler, monkeypatch)
    vectors = c.embed(["a", "b", "c", "d", "e"])
    assert sizes == [2, 2, 1]
    assert vectors == [[0.0], [1.0], [0.0], [1.0], [0.0]]


def test_stream_reads_sse_and_skips_keepalives(monkeypatch):
    body = (
        ": OPENROUTER PROCESSING\n\n"
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    c = make_client(lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}), monkeypatch)
    assert "".join(c.stream([{"role": "user", "content": "x"}])) == "Hello"


def test_stream_error_event_mid_answer(monkeypatch):
    body = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: {"error":{"code":429,"message":"busy"}}\n\n'
    c = make_client(lambda r: httpx.Response(200, text=body), monkeypatch)
    with pytest.raises(llm.RateLimited):
        list(c.stream([{"role": "user", "content": "x"}]))


def test_missing_key_is_a_clear_error():
    with pytest.raises(llm.AIError, match="OPENROUTER_API_KEY"):
        llm.OpenRouterClient(Settings(openrouter_api_key=""))
