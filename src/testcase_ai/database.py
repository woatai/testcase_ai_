"""定义 SQLAlchemy 持久化模型和事务边界。

MySQL 保存项目版本、知识块元数据、生成记录和审批记录；向量本身由 Milvus 保存。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    make_url,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    """生成带 UTC 时区的当前时间，供数据库默认时间字段复用。"""

    return datetime.now(UTC)


class Base(DeclarativeBase):
    """所有 ORM 表模型的声明式基类。"""

    pass


class ProjectRecord(Base):
    """记录业务项目及其当前生效的知识版本。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class KnowledgeRevisionRecord(Base):
    """记录一次知识构建的 fingerprint、状态和统计信息。"""

    __tablename__ = "knowledge_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="building")
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_revisions_project_status", "project_id", "status"),
        Index("ix_revisions_project_fingerprint", "project_id", "fingerprint"),
    )


class KnowledgeChunkRecord(Base):
    """保存可检索知识块的正文、来源和业务元数据。"""

    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_revisions.id", ondelete="CASCADE"), nullable=False
    )
    logical_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_logical_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_path: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("revision_id", "logical_id", name="uq_chunk_revision_logical"),
        Index("ix_chunks_project_revision", "project_id", "revision_id"),
        Index("ix_chunks_source_type", "project_id", "revision_id", "source_type"),
    )


class RequirementRecord(Base):
    """预留的需求持久化表，用于保存归一化后的原始需求。"""

    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class GenerationRecord(Base):
    """保存生成请求、结果、模型和父子任务关系，支持查询与继续生成。"""

    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_generations_project_created", "project_id", "created_at"),)


class ApprovalRecord(Base):
    """保存已完成的人工审批，唯一 generation_id 用于保证审批幂等。"""

    __tablename__ = "approval_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_case_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    target_files: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("generation_id", name="uq_approval_generation"),)


class Database:
    """封装 SQLAlchemy Engine、Session 工厂和统一事务上下文。"""

    def __init__(self, url: str) -> None:
        """按数据库 URL 创建连接池；SQLite 文件模式会预先创建父目录。"""

        parsed_url = make_url(url)
        if parsed_url.drivername.startswith("sqlite") and parsed_url.database not in {None, ":memory:"}:
            Path(parsed_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        """补建当前 ORM 声明中尚不存在的表；正式结构演进仍由 Alembic 管理。"""

        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """提供自动提交、异常回滚并始终关闭连接的事务上下文。"""

        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
