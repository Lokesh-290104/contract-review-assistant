"""Unit tests for the pure parts: parsing, chunking, quote checks, retrieval scoring, risk rules, model answers."""

import io

import pytest

from app.chunking import MAX_CHUNK_CHARS, chunk_text
from app.parsing import ParseError, detect_type, extract_text, page_for_offset
from app.quotes import clean_model_quote, find_quote, quoted_passages
from app.retrieval import keyword_scores, rrf_merge
from app.review import parse_model_answer
from app.risk import risk_flag


# --- parsing ---------------------------------------------------------------
def test_detect_type_checks_extension_and_magic_bytes():
    assert detect_type("a.PDF", b"%PDF-1.7 ...") == "pdf"
    assert detect_type("a.docx", b"PK\x03\x04") == "docx"
    assert detect_type("a.txt", b"hello") == "txt"
    with pytest.raises(ParseError):
        detect_type("a.exe", b"MZ")
    with pytest.raises(ParseError):
        detect_type("fake.pdf", b"hello")
    with pytest.raises(ParseError):
        detect_type("fake.docx", b"%PDF")
    with pytest.raises(ParseError):
        detect_type("bin.txt", b"\x00\x01\x02")


def test_txt_form_feed_makes_pages():
    data = "Page one text is here.\fPage two text is here.".encode()
    out = extract_text("txt", data)
    assert out.has_pages
    assert page_for_offset(out.page_starts, out.full_text.index("two")) == 2


def test_empty_text_is_rejected():
    with pytest.raises(ParseError, match="No readable text"):
        extract_text("txt", b"   ")


def test_pdf_pages_are_kept():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 800, "1. TERM This agreement starts on 1 May 2026.")
    c.showPage()
    c.drawString(72, 800, "2. LAW This agreement is governed by the laws of India.")
    c.save()
    out = extract_text("pdf", buf.getvalue())
    assert out.has_pages and len(out.page_starts) == 2
    assert page_for_offset(out.page_starts, out.full_text.index("governed")) == 2


def test_docx_has_no_pages_but_keeps_text():
    import docx

    d = docx.Document()
    d.add_paragraph("1. Governing Law")
    d.add_paragraph("This agreement is governed by the laws of England.")
    buf = io.BytesIO()
    d.save(buf)
    out = extract_text("docx", buf.getvalue())
    assert not out.has_pages
    assert "laws of England" in out.full_text


def test_damaged_pdf_gives_plain_error():
    with pytest.raises(ParseError):
        extract_text("pdf", b"%PDF-1.4 garbage garbage")


# --- chunking --------------------------------------------------------------
def test_chunks_follow_clause_numbers(services_txt):
    text = extract_text("txt", services_txt)
    chunks = chunk_text(text.full_text, text.page_starts, text.has_pages)
    sections = [c.section for c in chunks]
    assert "2.2" in sections and "4.1" in sections and "7.1" in sections
    renewal = next(c for c in chunks if c.section == "2.2")
    assert "automatically renew" in renewal.text
    assert renewal.heading == "Term"  # parent heading carried to sub-clauses
    # offsets point at the exact text
    for c in chunks:
        assert text.full_text[c.start_char : c.end_char] == c.text


def test_heading_only_lines_merge_into_next_clause():
    full = "1. TERMINATION\n1.1 Either party may terminate this agreement on thirty days notice in writing to the other."
    chunks = chunk_text(full, [0], False)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("1. TERMINATION")


def test_amounts_at_line_start_are_not_headings():
    full = "1. Payment\nThe client pays the fee listed below every month on time.\n30 days after invoice the fee is due and payable in full."
    chunks = chunk_text(full, [0], False)
    assert len(chunks) == 1


def test_long_clause_is_split_with_same_section():
    body = " ".join(["The supplier shall deliver the goods as agreed."] * 120)
    full = f"5. Delivery\n{body}"
    chunks = chunk_text(full, [0], False)
    assert len(chunks) > 1
    assert all(c.section == "5" for c in chunks)
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)


def test_chunk_pages_from_offsets():
    page1 = "1. Term\nThis agreement starts on 1 May 2026 and lasts for one full year."
    page2 = "2. Law\nThis agreement is governed by the laws of India and its courts."
    full = page1 + "\n\n" + page2
    chunks = chunk_text(full, [0, len(page1) + 2], True)
    assert [(c.section, c.page_start) for c in chunks] == [("1", 1), ("2", 2)]


# --- quotes ----------------------------------------------------------------
CONTRACT = "4.1 Either Party may terminate this Agreement by giving ninety (90) days’ written\nnotice.  Other text."


def test_exact_quote_is_found_and_returns_contract_text():
    m = find_quote(CONTRACT, "Either Party may terminate this Agreement by giving ninety (90) days' written notice.")
    assert m is not None
    assert m.text == CONTRACT[m.start : m.end]
    assert "’" in m.text  # the contract's own apostrophe, not the model's


def test_whitespace_case_and_wrapping_quotes_are_forgiven():
    assert find_quote(CONTRACT, '"either party may terminate this agreement  by giving"') is not None


def test_changed_words_are_rejected():
    assert find_quote(CONTRACT, "Either Party may terminate this Agreement by giving sixty (60) days") is None
    assert find_quote(CONTRACT, "Either Party may end this Agreement") is None


LIABILITY = (
    "6.1 Neither Party shall be liable for any indirect damages, including loss of profits.\n"
    "6.2 Each Party's total liability shall not exceed the fees paid in the twelve (12) months before the claim.\n"
    "7. GOVERNING LAW\n7.1 This Agreement is governed by the laws of India."
)


def test_quote_that_skips_a_clause_number_is_accepted_with_the_number_shown():
    model = "Neither Party shall be liable for any indirect damages, including loss of profits. Each Party's total liability shall not exceed the fees paid in the twelve (12) months before the claim."
    m = find_quote(LIABILITY, model)
    assert m is not None
    assert "\n6.2 Each Party" in m.text  # the contract's own text, clause number included
    assert m.text == LIABILITY[m.start : m.end]


def test_stitching_rejects_anything_but_a_clause_number_in_the_gap():
    # Skips a heading and a clause number: real words are missing, so it is not accepted.
    model = "Each Party's total liability shall not exceed the fees paid in the twelve (12) months before the claim. This Agreement is governed by the laws of India."
    assert find_quote(LIABILITY, model) is None
    # A dropped real word ("a") between sentences is not a clause number.
    text = "The fee is due monthly. A late fee applies after thirty days."
    assert find_quote(text, "The fee is due monthly. late fee applies after thirty days.") is None
    assert find_quote("The fee is due monthly.\n(b) Late fee applies after thirty days.", "The fee is due monthly. Late fee applies after thirty days.") is not None
    # Sentences in the wrong order are not accepted either.
    model = "Each Party's total liability shall not exceed the fees paid in the twelve (12) months before the claim. Neither Party shall be liable for any indirect damages, including loss of profits."
    assert find_quote(LIABILITY, model) is None


def test_ellipsis_and_too_short_quotes_are_rejected():
    assert find_quote(CONTRACT, "Either Party may ... written notice") is None
    assert find_quote(CONTRACT, "Either") is None


def test_prefers_match_inside_shown_chunks():
    text = "A. The fee is payable monthly in advance. B. The fee is payable monthly in advance."
    second = text.rindex("The fee")
    m = find_quote(text, "The fee is payable monthly in advance.", [(second, len(text))])
    assert m.start == second


def test_clean_model_quote_and_quoted_passages():
    assert clean_model_quote(' “hello there” ') == "hello there"
    assert quoted_passages('It says "Either party may terminate" (Section 4).') == ["Either party may terminate"]
    assert quoted_passages('short "no"') == []


# --- retrieval scoring -----------------------------------------------------
def test_keyword_scores_rank_strong_phrases_higher():
    scores = keyword_scores(
        ["governed by the laws of India", "the courts of Delhi", "payment is due"],
        (r"\bgoverned by\b", r"\blaws of\b", r"\bcourts? of\b"),
    )
    assert scores[0] > scores[1] > scores[2] == 0


def test_rrf_rewards_agreement_between_rankings():
    fused = rrf_merge([[1, 2, 3], [3, 1, 4]])
    assert max(fused, key=fused.get) == 1
    assert fused[3] > fused[2]


# --- model answers ---------------------------------------------------------
def test_parse_model_answer_handles_fences_and_bad_json():
    a = parse_model_answer('```json\n{"status": "Found", "summary": "x", "quote": "y"}\n```')
    assert a.status == "found"
    assert parse_model_answer("no json here") is None
    assert parse_model_answer('{"status": "maybe"}') is None
    assert parse_model_answer('{"status": "not found", "summary": "", "quote": ""}').status == "not_found"


# --- risk ------------------------------------------------------------------
@pytest.mark.parametrize(
    "key,status,quote,expect",
    [
        ("liability", "not_found", None, "unlimited"),
        ("liability", "found", "liability shall not exceed the fees paid in 12 months", None),
        ("liability", "found", "neither party is liable for indirect damages", "no cap"),
        ("auto_renewal", "found", "will automatically renew", "Renews automatically"),
        ("auto_renewal", "not_found", None, None),
        ("termination", "found", "one hundred twenty (120) days' notice", "120 days"),
        ("termination", "found", "thirty (30) days' notice", None),
        ("payment", "found", "within ninety (90) days of invoice", "90 days"),
        ("governing_law", "not_found", None, "No governing law"),
        ("parties", "unclear", None, "check this clause"),
    ],
)
def test_risk_rules(key, status, quote, expect):
    flag = risk_flag(key, status, quote)
    if expect is None:
        assert flag is None
    else:
        assert flag and expect.lower() in flag.lower()
