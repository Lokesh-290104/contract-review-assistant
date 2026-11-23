"""Database tables. Every table carries `user_id` so every query can be scoped to the owner."""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ContractStatus:
    PROCESSING = "processing"
    REVIEWED = "reviewed"
    FAILED = "failed"


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(16))  # pdf | docx | txt
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default=ContractStatus.PROCESSING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whole extracted text, and the char offset where each page starts (page 1 = index 0).
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    page_starts: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True, deferred=True)
    has_pages: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    file: Mapped["ContractFile"] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    chunks: Mapped[list["Chunk"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    findings: Mapped[list["Finding"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    messages: Mapped[list["ChatMessage"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class ContractFile(Base):
    """The original upload, kept apart so list queries never load the bytes."""

    __tablename__ = "contract_files"

    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Chunk(Base):
    """One clause (or part of a long clause) with its location and embedding."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(64))
    idx: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().embed_dim), nullable=True, deferred=True
    )

    __table_args__ = (Index("ix_chunks_user_contract", "user_id", "contract_id"),)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(64))
    item_key: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # found | not_found | unclear
    summary: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_findings_user_contract", "user_id", "contract_id"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (Index("ix_chat_user_contract", "user_id", "contract_id"),)
