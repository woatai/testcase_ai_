"""知识同步主链路：发现文件、解析分块、生成向量并原子发布新知识版本。"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from testcase_ai.contracts import KnowledgeSyncResult
from testcase_ai.database import (
    Database,
    KnowledgeChunkRecord,
    KnowledgeRevisionRecord,
    ProjectRecord,
)
from testcase_ai.manifest import discover_source_files, load_manifest
from testcase_ai.parsers import ParsedChunk, parse_knowledge_file
from testcase_ai.providers import OpenAICompatibleEmbeddingProvider
from testcase_ai.vector_store import VectorItem, VectorStore


class KnowledgeService:
    """协调 Manifest、解析器、MySQL、Embedding 服务和向量库完成知识构建。"""

    def __init__(
        self,
        *,
        database: Database,
        embeddings: OpenAICompatibleEmbeddingProvider,
        vector_store: VectorStore,
        chunk_size: int = 900,
        chunk_overlap: int = 120,
        embedding_batch_size: int = 32,
    ) -> None:
        """注入知识构建依赖，并保存切块及 Embedding 批处理参数。"""

        self.database = database
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_batch_size = embedding_batch_size

    @staticmethod
    def _fingerprint(project_dir: Path, manifest_payload: dict, files: list[tuple[str, Path]]) -> str:
        """综合清单、来源类型、相对路径和文件内容生成可重复的版本指纹。"""

        digest = hashlib.sha256(
            json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        for source_type, path in sorted(files, key=lambda item: item[1].as_posix()):
            digest.update(source_type.encode("utf-8"))
            digest.update(path.relative_to(project_dir).as_posix().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def sync(self, project_dir: Path) -> KnowledgeSyncResult:
        """同步一个业务知识包并发布新 revision。

        指纹未变化时直接返回当前版本；有变化时先创建 ``building`` 版本，解析并落库，
        Embedding 与 Milvus 写入成功后才切换 ``active_revision_id``。失败版本会标记为
        ``failed``，原有生效版本继续可用。
        """

        project_dir = project_dir.resolve()
        manifest = load_manifest(project_dir)
        source_files: list[tuple[str, Path]] = []
        source_type_by_path = {}
        for source in manifest.sources:
            for path in discover_source_files(project_dir, source):
                source_files.append((source.type.value, path))
                source_type_by_path[path.resolve()] = source.type
        fingerprint = self._fingerprint(project_dir, manifest.model_dump(mode="json"), source_files)
        manifest_hash = hashlib.sha256(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        with self.database.session() as session:
            project = session.get(ProjectRecord, manifest.id)
            if project is None:
                project = ProjectRecord(
                    id=manifest.id,
                    name=manifest.name,
                    manifest_version=manifest.version,
                    manifest_hash=manifest_hash,
                )
                session.add(project)
                session.flush()
            elif project.active_revision_id:
                active = session.get(KnowledgeRevisionRecord, project.active_revision_id)
                if active and active.fingerprint == fingerprint and active.status == "published":
                    return KnowledgeSyncResult(
                        project_id=manifest.id,
                        revision_id=active.id,
                        source_count=active.source_count,
                        chunk_count=active.chunk_count,
                        indexed_count=active.chunk_count,
                        unchanged=True,
                    )
                project.name = manifest.name
                project.manifest_version = manifest.version
                project.manifest_hash = manifest_hash

            revision_id = str(uuid.uuid4())
            revision = KnowledgeRevisionRecord(
                id=revision_id,
                project_id=manifest.id,
                fingerprint=fingerprint,
                status="building",
                source_count=len(source_files),
            )
            session.add(revision)

        parsed: list[ParsedChunk] = []
        try:
            for _, path in source_files:
                relative_path = path.relative_to(project_dir).as_posix()
                parsed.extend(
                    parse_knowledge_file(
                        path,
                        relative_path=relative_path,
                        source_type=source_type_by_path[path.resolve()],
                        excel_mapping=manifest.excel,
                        chunk_size=self.chunk_size,
                        overlap=self.chunk_overlap,
                    )
                )
            if not parsed:
                raise ValueError("knowledge package produced no indexable chunks")

            records = [
                KnowledgeChunkRecord(
                    id=str(uuid.uuid4()),
                    project_id=manifest.id,
                    revision_id=revision_id,
                    logical_id=chunk.logical_id,
                    parent_logical_id=chunk.parent_logical_id,
                    source_path=chunk.source_path,
                    source_type=chunk.source_type.value,
                    section=chunk.section,
                    module=chunk.module,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    metadata_json=chunk.metadata,
                )
                for chunk in parsed
            ]
            with self.database.session() as session:
                session.add_all(records)
                revision = session.get(KnowledgeRevisionRecord, revision_id)
                if revision:
                    revision.chunk_count = len(records)

            items: list[VectorItem] = []
            for start in range(0, len(records), self.embedding_batch_size):
                batch = records[start : start + self.embedding_batch_size]
                vectors = self.embeddings.embed([record.content for record in batch])
                items.extend(
                    VectorItem(
                        id=record.id,
                        vector=vector,
                        project_id=manifest.id,
                        revision_id=revision_id,
                        source_type=record.source_type,
                        module=record.module,
                    )
                    for record, vector in zip(batch, vectors, strict=True)
                )
            self.vector_store.upsert(items)

            with self.database.session() as session:
                revision = session.get(KnowledgeRevisionRecord, revision_id)
                project = session.get(ProjectRecord, manifest.id)
                if revision is None or project is None:
                    raise RuntimeError("knowledge revision disappeared during publish")
                revision.status = "published"
                revision.published_at = datetime.now(UTC)
                project.active_revision_id = revision_id
            return KnowledgeSyncResult(
                project_id=manifest.id,
                revision_id=revision_id,
                source_count=len(source_files),
                chunk_count=len(records),
                indexed_count=len(items),
            )
        except Exception as exc:
            self.vector_store.delete_revision(manifest.id, revision_id)
            with self.database.session() as session:
                revision = session.get(KnowledgeRevisionRecord, revision_id)
                if revision:
                    revision.status = "failed"
                    revision.error = str(exc)[:4000]
            raise
