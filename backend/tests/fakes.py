"""Offline stand-in for the AI service, used only by the tests (no network, deterministic).

Embeddings are word-hash vectors. Review answers pick the first sentence matching the
item's keywords; chat answers quote the first suitable sentence of the first clause.
Tests can override answers with `responder` / `streamer`.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterator

from app.llm import Message

_WORD = re.compile(r"[a-z0-9]+")


class FakeAI:
    """Deterministic stand-in used by tests and by `AI_PROVIDER=fake`."""

    def __init__(self, dim: int, responder=None, streamer=None):
        self.dim = dim
        self.responder = responder
        self.streamer = streamer
        self.embed_calls: list[int] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(len(texts))
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for w in _WORD.findall(text.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out

    def complete(self, messages: list[Message], max_tokens: int = 600) -> str:
        if self.responder:
            return self.responder(messages)
        return _fake_review_answer(messages[-1]["content"])

    def stream(self, messages: list[Message], max_tokens: int = 900) -> Iterator[str]:
        if self.streamer:
            yield from self.streamer(messages)
            return
        text = _fake_chat_answer(messages[-1]["content"])
        for word in text.split(" "):
            yield word + " "


def _chunks_in_prompt(prompt: str) -> list[str]:
    return re.findall(r"<chunk[^>]*>\n(.*?)\n</chunk>", prompt, flags=re.S)


def _fake_review_answer(prompt: str) -> str:
    from app.checklist import ITEMS_BY_KEY

    m = re.search(r"CHECKLIST ITEM KEY: (\w+)", prompt)
    item = ITEMS_BY_KEY.get(m.group(1)) if m else None
    if item:
        for pattern in item.keywords[:3]:
            for chunk in _chunks_in_prompt(prompt):
                for sentence in re.split(r"(?<=[.;])\s+", chunk):
                    if re.search(pattern, sentence, flags=re.I) and len(sentence) > 20:
                        s = sentence.strip()
                        return json.dumps(
                            {"status": "found", "summary": f"{s[:110]}", "quote": s}
                        )
    return json.dumps({"status": "not_found", "summary": "Not found in the contract.", "quote": ""})


def _fake_chat_answer(prompt: str) -> str:
    chunks = _chunks_in_prompt(prompt)
    if not chunks:
        return "The contract does not say."
    for chunk in chunks:
        for sentence in re.split(r"(?<=[.;])\s+", chunk.strip()):
            s = " ".join(sentence.split())
            if len(s) > 20 and '"' not in s and "“" not in s:
                return f'The closest clause says: "{s}"'
    return "The contract doesn't say."
