"""Turn an uploaded file into plain text, keeping page boundaries when the format has them."""

import io
import re
import zipfile
from dataclasses import dataclass

ALLOWED_TYPES = {"pdf", "docx", "txt"}


class ParseError(Exception):
    """The file could not be read; the message is shown to the user."""


@dataclass
class ExtractedText:
    full_text: str
    page_starts: list[int]  # char offset where each page begins; [0] when there are no pages
    has_pages: bool


def detect_type(filename: str, head: bytes) -> str:
    """Pick the file type from the extension, then confirm it with the file's first bytes."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_TYPES:
        raise ParseError("Only PDF, DOCX and TXT files are supported.")
    if ext == "pdf" and not head.startswith(b"%PDF"):
        raise ParseError("This file is not a valid PDF.")
    if ext == "docx" and not head.startswith(b"PK"):
        raise ParseError("This file is not a valid DOCX (Word) document.")
    if ext == "txt" and b"\x00" in head[:4096]:
        raise ParseError("This TXT file looks like binary data, not text.")
    return ext


def _clean(text: str) -> str:
    # Postgres text cannot hold NUL characters, which some PDFs contain.
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _join_pages(pages: list[str], has_pages: bool) -> ExtractedText:
    parts: list[str] = []
    starts: list[int] = []
    pos = 0
    for page in pages:
        starts.append(pos)
        parts.append(page)
        pos += len(page) + 2  # the "\n\n" separator
    full = "\n\n".join(parts)
    return ExtractedText(full_text=full, page_starts=starts or [0], has_pages=has_pages)


def _pdf(data: bytes) -> ExtractedText:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 - pypdf raises several types here
                raise ParseError("This PDF is password-protected.") from exc
        pages = [_clean(page.extract_text() or "") for page in reader.pages]
    except PdfReadError as exc:
        raise ParseError("This PDF is damaged and could not be read.") from exc
    return _join_pages(pages, has_pages=True)


def _docx(data: bytes) -> ExtractedText:
    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        # ValueError: a zip that is not a Word file (e.g. a renamed .xlsx).
        raise ParseError("This DOCX file is damaged or is not a Word document.") from exc
    lines = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    # Word files have no fixed pages; locations use section numbers instead.
    return _join_pages([_clean("\n".join(lines))], has_pages=False)


def _txt(data: bytes) -> ExtractedText:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    # A form feed marks a page break in plain-text exports.
    if "\f" in text:
        return _join_pages([_clean(p) for p in text.split("\f")], has_pages=True)
    return _join_pages([_clean(text)], has_pages=False)


def extract_text(kind: str, data: bytes) -> ExtractedText:
    result = {"pdf": _pdf, "docx": _docx, "txt": _txt}[kind](data)
    if len(result.full_text.strip()) < 20:
        raise ParseError(
            "No readable text was found. If this is a scanned PDF, please upload a text-based PDF, DOCX or TXT."
        )
    return result


def page_for_offset(page_starts: list[int], offset: int) -> int:
    """1-based page number that holds the given char offset."""
    page = 1
    for i, start in enumerate(page_starts):
        if offset >= start:
            page = i + 1
        else:
            break
    return page
