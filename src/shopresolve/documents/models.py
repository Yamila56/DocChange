from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shopresolve.models import Base


class Workspace(Base):
    __tablename__ = "doc_workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)


class Revision(Base):
    __tablename__ = "doc_revisions"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("doc_workspaces.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    documents: Mapped[dict] = mapped_column(JSON)
    index: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


class ChangeSet(Base):
    __tablename__ = "doc_changesets"
    __table_args__ = (UniqueConstraint("run_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("doc_workspaces.id"), index=True)
    base_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    published_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
