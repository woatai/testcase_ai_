"""混合检索主链路：Dense 向量召回与中文 BM25 召回经 RRF 融合后返回证据。"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from testcase_ai.contracts import RetrievalRequestV1, RetrievedEvidence, SourceType
from testcase_ai.database import Database, KnowledgeChunkRecord, ProjectRecord
from testcase_ai.manifest import resolve_project
from testcase_ai.providers import OpenAICompatibleEmbeddingProvider
from testcase_ai.vector_store import VectorStore


def tokenize(text: str) -> list[str]:
    """生成适合中英混合内容的 BM25 词元，包括英文词、连续中文串和中文二元组。"""

    normalized = (text or "").lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9_./:-]*", normalized)
    chinese = "".join(character for character in normalized if "\u4e00" <= character <= "\u9fff")
    tokens.extend(chinese)
    tokens.extend(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return [token for token in tokens if token]


def bm25_scores(query: str, documents: list[str], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    """计算查询对每篇文档的 BM25 分数，返回顺序与输入文档严格一致。"""

    if not documents:
        return []
    tokenized = [tokenize(document) for document in documents]
    query_tokens = list(dict.fromkeys(tokenize(query)))
    lengths = [len(tokens) for tokens in tokenized]
    average_length = sum(lengths) / len(lengths) if lengths else 1.0
    frequencies = [Counter(tokens) for tokens in tokenized]
    scores: list[float] = []
    for frequency, length in zip(frequencies, lengths, strict=True):
        score = 0.0
        for token in query_tokens:
            count = frequency.get(token, 0)
            if not count:
                continue
            df = sum(1 for item in frequencies if token in item)
            inverse = math.log(1 + (len(documents) - df + 0.5) / (df + 0.5))
            denominator = count + k1 * (1 - b + b * length / max(average_length, 1))
            score += inverse * count * (k1 + 1) / denominator
        scores.append(score)
    return scores


class RetrievalService:
    """在当前生效知识版本内执行带项目、模块和来源隔离的混合检索。"""

    def __init__(
        self,
        *,
        database: Database,
        embeddings: OpenAICompatibleEmbeddingProvider,
        vector_store: VectorStore,
        projects_root: Path | None = None,
    ) -> None:
        """注入元数据数据库、Embedding Provider、向量库和可选项目清单根目录。"""

        self.database = database
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.projects_root = projects_root.resolve() if projects_root else None

    def search(self, request: RetrievalRequestV1) -> list[RetrievedEvidence]:
        """返回经过 RRF 排序的 TopK 证据。

        MySQL 先确定当前 revision 和候选正文；查询向量用于 Milvus Dense 检索，同一批正文
        同时执行 BM25。两路名次以 ``1 / (rrf_k + rank)`` 融合，并保留各自排名便于调试。
        """

        with self.database.session() as session:
            project = session.get(ProjectRecord, request.project_id)
            if project is None or not project.active_revision_id:
                raise ValueError(f"project has no active knowledge revision: {request.project_id}")
            revision_id = project.active_revision_id
            statement = select(KnowledgeChunkRecord).where(
                KnowledgeChunkRecord.project_id == request.project_id,
                KnowledgeChunkRecord.revision_id == revision_id,
            )
            if request.module:
                statement = statement.where(KnowledgeChunkRecord.module == request.module)
            if request.source_types:
                statement = statement.where(
                    KnowledgeChunkRecord.source_type.in_([item.value for item in request.source_types])
                )
            chunks = list(session.scalars(statement))

        if not chunks:
            return []
        if self.projects_root:
            _, manifest = resolve_project(self.projects_root, request.project_id)
            dense_top_k = manifest.retrieval.dense_top_k
            bm25_top_k = manifest.retrieval.bm25_top_k
            default_top_k = manifest.retrieval.fused_top_k
            rrf_k = manifest.retrieval.rrf_k
        else:
            dense_top_k = 20
            bm25_top_k = 20
            default_top_k = 12
            rrf_k = 60
        by_id = {chunk.id: chunk for chunk in chunks}
        vector = self.embeddings.embed([request.query])[0]
        dense_hits = self.vector_store.search(
            vector,
            project_id=request.project_id,
            revision_id=revision_id,
            top_k=max(dense_top_k, request.top_k or 0),
            module=request.module,
            source_types=[item.value for item in request.source_types] or None,
        )
        dense_hits = [hit for hit in dense_hits if hit.id in by_id]

        sparse_scores = bm25_scores(request.query, [chunk.content for chunk in chunks])
        sparse_hits = sorted(
            ((chunks[index].id, score) for index, score in enumerate(sparse_scores) if score > 0),
            key=lambda item: item[1],
            reverse=True,
        )[: max(bm25_top_k, request.top_k or 0)]

        ranks: dict[str, dict[str, int]] = defaultdict(dict)
        fused: dict[str, float] = defaultdict(float)
        for rank, hit in enumerate(dense_hits, start=1):
            ranks[hit.id]["dense"] = rank
            fused[hit.id] += 1 / (rrf_k + rank)
        for rank, (chunk_id, _) in enumerate(sparse_hits, start=1):
            ranks[chunk_id]["sparse"] = rank
            fused[chunk_id] += 1 / (rrf_k + rank)

        top_k = request.top_k or default_top_k
        ordered = sorted(fused, key=lambda chunk_id: fused[chunk_id], reverse=True)[:top_k]
        return [
            RetrievedEvidence(
                evidence_id=chunk_id,
                logical_id=by_id[chunk_id].logical_id,
                case_id=(by_id[chunk_id].metadata_json or {}).get("case_id"),
                content=by_id[chunk_id].content,
                source=by_id[chunk_id].source_path,
                source_type=SourceType(by_id[chunk_id].source_type),
                section=by_id[chunk_id].section,
                module=by_id[chunk_id].module,
                score=fused[chunk_id],
                dense_rank=ranks[chunk_id].get("dense"),
                sparse_rank=ranks[chunk_id].get("sparse"),
            )
            for chunk_id in ordered
        ]
