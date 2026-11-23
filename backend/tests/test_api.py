"""API tests: upload, review pipeline, quote checking, chat streaming, and user isolation."""

import json

import pytest

from app import llm
from app.models import Chunk
from app.db import new_session


def upload(client, headers, data: bytes, name: str = "contract.txt"):
    return client.post("/api/contracts", headers=headers, files={"file": (name, data, "application/octet-stream")})


def sse_events(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


# --- upload + review --------------------------------------------------------
def test_upload_reviews_contract_and_verifies_quotes(client, auth, services_txt):
    r = upload(client, auth("user_a"), services_txt, "msa.txt")
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    listing = client.get("/api/contracts", headers=auth("user_a")).json()
    assert listing[0]["filename"] == "msa.txt"
    assert listing[0]["size_bytes"] == len(services_txt)
    assert listing[0]["status"] == "reviewed"

    detail = client.get(f"/api/contracts/{cid}", headers=auth("user_a")).json()
    assert [f["item_key"] for f in detail["findings"]] == [
        "parties", "term", "payment", "termination", "liability", "confidentiality", "governing_law", "auto_renewal",
    ]
    assert detail["clauses"], "clauses are returned for the clause viewer"
    full = {c["id"]: c for c in detail["clauses"]}
    for f in detail["findings"]:
        if f["status"] == "found":
            assert f["verified"] and f["quote"] in services_txt.decode()
            # "Clicking the quote shows the full clause": the quote sits inside the linked clause.
            assert f["quote"] in full[f["chunk_id"]]["text"]
            assert f["section"]
    gl = next(f for f in detail["findings"] if f["item_key"] == "governing_law")
    assert gl["status"] == "found" and "laws of India" in gl["quote"]


def test_made_up_quote_is_marked_unclear(client, auth, services_txt, fake_ai):
    fake_ai.responder = lambda m: json.dumps(
        {"status": "found", "summary": "Ends with 15 days' notice.", "quote": "Either Party may terminate on fifteen days notice."}
    )
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    findings = client.get(f"/api/contracts/{cid}", headers=auth("user_a")).json()["findings"]
    assert {f["status"] for f in findings} == {"unclear"}
    assert all(f["quote"] is None and not f["verified"] and f["note"] for f in findings)


def test_not_found_is_honest(client, auth, nda_txt, fake_ai):
    fake_ai.responder = lambda m: json.dumps({"status": "not_found", "summary": "anything", "quote": ""})
    cid = upload(client, auth("user_a"), nda_txt).json()["id"]
    findings = client.get(f"/api/contracts/{cid}", headers=auth("user_a")).json()["findings"]
    assert all(f["status"] == "not_found" and f["summary"] == "Not found in the contract." for f in findings)
    liability = next(f for f in findings if f["item_key"] == "liability")
    assert "unlimited" in liability["risk"]


def test_embeddings_are_sent_in_batches(client, auth, services_txt, fake_ai):
    upload(client, auth("user_a"), services_txt)
    # one call for all chunks + one call for the 8 checklist queries
    assert len(fake_ai.embed_calls) == 2
    assert fake_ai.embed_calls[0] > 10 and fake_ai.embed_calls[1] == 8


def test_rate_limit_marks_failed_with_clear_message_and_retry_works(client, auth, services_txt, fake_ai):
    def limited(messages):
        raise llm.RateLimited(llm.RATE_LIMIT_MESSAGE)

    fake_ai.responder = limited
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    c = client.get(f"/api/contracts/{cid}", headers=auth("user_a")).json()
    assert c["status"] == "failed" and "rate-limited" in c["error"]

    fake_ai.responder = None
    calls_before = len(fake_ai.embed_calls)
    r = client.post(f"/api/contracts/{cid}/review", headers=auth("user_a"))
    assert r.status_code == 202
    c = client.get(f"/api/contracts/{cid}", headers=auth("user_a")).json()
    assert c["status"] == "reviewed" and c["error"] is None
    # chunk embeddings were kept, so only the 8 queries are embedded again
    assert fake_ai.embed_calls[calls_before:] == [8]


def test_pdf_and_docx_uploads_work(client, auth):
    import io

    import docx
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.drawString(72, 800, "1. Governing Law")
    pdf.drawString(72, 780, "This Agreement shall be governed by the laws of Singapore.")
    pdf.showPage()
    pdf.save()
    r = upload(client, auth("user_a"), buf.getvalue(), "c.pdf")
    assert r.status_code == 201
    d = client.get(f"/api/contracts/{r.json()['id']}", headers=auth("user_a")).json()
    gl = next(f for f in d["findings"] if f["item_key"] == "governing_law")
    assert gl["status"] == "found" and gl["page"] == 1

    doc = docx.Document()
    doc.add_paragraph("7. Governing Law")
    doc.add_paragraph("This Agreement shall be governed by the laws of England and Wales.")
    buf = io.BytesIO()
    doc.save(buf)
    r = upload(client, auth("user_a"), buf.getvalue(), "c.docx")
    d = client.get(f"/api/contracts/{r.json()['id']}", headers=auth("user_a")).json()
    gl = next(f for f in d["findings"] if f["item_key"] == "governing_law")
    assert gl["status"] == "found" and gl["page"] is None and gl["section"] == "7"


# --- upload validation -----------------------------------------------------
@pytest.mark.parametrize(
    "name,data,code",
    [
        ("virus.exe", b"MZ....", 415),
        ("fake.pdf", b"just text", 415),
        ("empty.txt", b"", 422),
    ],
)
def test_bad_uploads_are_rejected(client, auth, name, data, code):
    r = upload(client, auth("user_a"), data, name)
    assert r.status_code == code
    assert r.json()["detail"]


def test_file_over_20mb_is_rejected(client, auth, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    r = upload(client, auth("user_a"), b"a" * (1024 * 1024 + 10), "big.txt")
    assert r.status_code == 413


def test_unreadable_contract_fails_with_message(client, auth):
    r = upload(client, auth("user_a"), b"%PDF-1.4 broken", "broken.pdf")
    c = client.get(f"/api/contracts/{r.json()['id']}", headers=auth("user_a")).json()
    assert c["status"] == "failed" and "PDF" in c["error"]


# --- chat ------------------------------------------------------------------
def test_chat_streams_and_history_persists(client, auth, services_txt):
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    r = client.post(f"/api/contracts/{cid}/chat", headers=auth("user_a"), json={"question": "How can we terminate?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "user" and kinds[-1] == "done" and kinds.count("token") > 3
    done = events[-1][1]
    assert done["citations"] and done["citations"][0]["verified"]

    history = client.get(f"/api/contracts/{cid}/messages", headers=auth("user_a")).json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["citations"][0]["verified"]


def test_chat_flags_unverified_quotes(client, auth, services_txt, fake_ai):
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    fake_ai.streamer = lambda m: iter(['It says "the client may cancel at any time without notice" (Section 9).'])
    r = client.post(f"/api/contracts/{cid}/chat", headers=auth("user_a"), json={"question": "Can we cancel?"})
    done = sse_events(r.text)[-1][1]
    assert done["citations"] == [{"quote": "the client may cancel at any time without notice", "verified": False}]


def test_chat_rate_limit_sends_error_event_and_drops_question(client, auth, services_txt, fake_ai):
    cid = upload(client, auth("user_a"), services_txt).json()["id"]

    def limited(messages):
        raise llm.RateLimited(llm.RATE_LIMIT_MESSAGE)
        yield  # pragma: no cover

    fake_ai.streamer = limited
    r = client.post(f"/api/contracts/{cid}/chat", headers=auth("user_a"), json={"question": "Who pays?"})
    kind, data = sse_events(r.text)[-1]
    assert kind == "error" and data["rate_limited"] is True
    assert client.get(f"/api/contracts/{cid}/messages", headers=auth("user_a")).json() == []


def test_chat_rejects_empty_question(client, auth, services_txt):
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    assert client.post(f"/api/contracts/{cid}/chat", headers=auth("user_a"), json={"question": "   "}).status_code == 422


# --- isolation -------------------------------------------------------------
def test_other_user_cannot_see_or_touch_contract(client, auth, services_txt):
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    b = auth("user_b")

    assert client.get("/api/contracts", headers=b).json() == []
    assert client.get(f"/api/contracts/{cid}", headers=b).status_code == 404
    assert client.post(f"/api/contracts/{cid}/review", headers=b).status_code == 404
    assert client.get(f"/api/contracts/{cid}/messages", headers=b).status_code == 404
    assert client.post(f"/api/contracts/{cid}/chat", headers=b, json={"question": "Who are the parties?"}).status_code == 404
    assert client.delete(f"/api/contracts/{cid}", headers=b).status_code == 404

    # still there for the owner
    assert client.get(f"/api/contracts/{cid}", headers=auth("user_a")).status_code == 200


def test_vector_search_is_scoped_to_user(client, auth, services_txt, nda_txt):
    from app.retrieval import retrieve

    a_id = upload(client, auth("user_a"), services_txt).json()["id"]
    upload(client, auth("user_b"), nda_txt)
    with new_session() as db:
        # user_b asking with user_a's contract id gets nothing back
        assert retrieve(db, "user_b", a_id, [0.1] * 64, ["law"]) == []
        mine = retrieve(db, "user_a", a_id, [0.1] * 64, ["law"])
        assert mine and all(c.user_id == "user_a" for c in mine)


def test_delete_removes_everything(client, auth, services_txt):
    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    client.post(f"/api/contracts/{cid}/chat", headers=auth("user_a"), json={"question": "Who are the parties?"})
    assert client.delete(f"/api/contracts/{cid}", headers=auth("user_a")).status_code == 204
    assert client.get(f"/api/contracts/{cid}", headers=auth("user_a")).status_code == 404
    with new_session() as db:
        assert db.query(Chunk).count() == 0


def test_bad_contract_id_is_422(client, auth):
    assert client.get("/api/contracts/not-a-uuid", headers=auth("user_a")).status_code == 422


def test_recover_interrupted_marks_processing_failed(client, auth, services_txt):
    from app import worker
    from app.models import Contract

    cid = upload(client, auth("user_a"), services_txt).json()["id"]
    with new_session() as db:
        db.get(Contract, __import__("uuid").UUID(cid)).status = "processing"
        db.commit()
    assert worker.recover_interrupted() == 1
    c = client.get(f"/api/contracts/{cid}", headers=auth("user_a")).json()
    assert c["status"] == "failed" and "Retry" in c["error"]
