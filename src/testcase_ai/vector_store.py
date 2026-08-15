"""定义向量存储抽象，以及测试用内存实现和生产用 Milvus 实现。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class VectorItem:
    """写入向量库的一条向量及其检索过滤字段。"""

    id: str
    vector: list[float]
    project_id: str
    revision_id: str
    source_type: str
    module: str | None


@dataclass(slots=True)
class VectorHit:
    """向量检索命中结果，只返回知识块 ID 和相似度分数。"""

    id: str
    score: float


class VectorStore(Protocol):
    """知识与检索服务依赖的最小向量库接口。"""

    def upsert(self, items: list[VectorItem]) -> None:
        """新增或覆盖一批向量。"""

        ...

    def search(
        self,
        vector: list[float],
        *,
        project_id: str,
        revision_id: str,
        top_k: int,
        module: str | None = None,
        source_types: list[str] | None = None,
    ) -> list[VectorHit]:
        """在过滤条件内按向量相似度返回 TopK 命中。"""

        ...


    def delete_revision(self, project_id: str, revision_id: str) -> None:
        """删除一次失败或废弃知识版本的全部向量。"""

        ...


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """计算等长非空向量的余弦相似度，非法或零向量返回 0。"""

    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class InMemoryVectorStore:
    """无需外部服务的内存向量库，主要用于单元测试。"""

    def __init__(self) -> None:
        """初始化以知识块 ID 为键的内存字典。"""

        self.items: dict[str, VectorItem] = {}

    def upsert(self, items: list[VectorItem]) -> None:
        """按 ID 写入或覆盖向量。"""

        self.items.update({item.id: item for item in items})

    def search(
        self,
        vector: list[float],
        *,
        project_id: str,
        revision_id: str,
        top_k: int,
        module: str | None = None,
        source_types: list[str] | None = None,
    ) -> list[VectorHit]:
        """在内存中过滤候选项、计算余弦相似度并返回 TopK。"""

        allowed = set(source_types or [])
        hits = []
        for item in self.items.values():
            if item.project_id != project_id or item.revision_id != revision_id:
                continue
            if module and item.module != module:
                continue
            if allowed and item.source_type not in allowed:
                continue
            hits.append(VectorHit(id=item.id, score=max(0.0, cosine_similarity(vector, item.vector))))
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]

    def delete_revision(self, project_id: str, revision_id: str) -> None:
        """移除属于指定项目和知识版本的所有内存向量。"""

        self.items = {
            key: item
            for key, item in self.items.items()
            if not (item.project_id == project_id and item.revision_id == revision_id)
        }


class MilvusVectorStore:
    """基于 MilvusClient 的向量库适配器，使用 COSINE 和强一致性。"""

    def __init__(self, *, uri: str, collection: str, token: str | None = None) -> None:
        """保存连接参数；真正的 MilvusClient 会延迟到首次访问时创建。"""

        self.uri = uri
        self.token = token
        self.collection = collection
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        """延迟建立并缓存 Milvus 客户端，避免应用装配阶段立即访问外部服务。"""

        if self._client is None:
            from pymilvus import MilvusClient

            kwargs: dict[str, Any] = {"uri": self.uri}
            if self.token:
                kwargs["token"] = self.token
            self._client = MilvusClient(**kwargs)
        return self._client

    def _ensure_collection(self, dimension: int) -> None:
        """确保集合存在且向量维度匹配；首次写入时按实际维度创建集合和索引。"""

        if self.client.has_collection(collection_name=self.collection):
            description = self.client.describe_collection(collection_name=self.collection)
            vector_field = next(
                (field for field in description.get("fields", []) if field.get("name") == "vector"), None
            )
            existing_dimension = int((vector_field or {}).get("params", {}).get("dim", dimension))
            if existing_dimension != dimension:
                raise ValueError(
                    "Milvus collection dimension mismatch: "
                    f"existing={existing_dimension} configured={dimension}"
                )
            return

        from pymilvus import DataType

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
        schema.add_field(field_name="project_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="revision_id", datatype=DataType.VARCHAR, max_length=36)
        schema.add_field(field_name="source_type", datatype=DataType.VARCHAR, max_length=32)
        schema.add_field(field_name="module", datatype=DataType.VARCHAR, max_length=128)
        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )

    def upsert(self, items: list[VectorItem]) -> None:
        """把向量和项目/版本过滤字段批量写入 Milvus。"""

        if not items:
            return
        self._ensure_collection(len(items[0].vector))
        payload = [
            {
                "id": item.id,
                "vector": item.vector,
                "project_id": item.project_id,
                "revision_id": item.revision_id,
                "source_type": item.source_type,
                "module": item.module or "",
            }
            for item in items
        ]
        self.client.upsert(collection_name=self.collection, data=payload)

    @staticmethod
    def _quote(value: str) -> str:
        """转义 Milvus 过滤表达式中的反斜杠和双引号。"""

        return value.replace("\\", "\\\\").replace('"', '\\"')

    def search(
        self,
        vector: list[float],
        *,
        project_id: str,
        revision_id: str,
        top_k: int,
        module: str | None = None,
        source_types: list[str] | None = None,
    ) -> list[VectorHit]:
        """按项目、版本及可选模块/来源过滤后执行 COSINE TopK 检索。"""

        expression = (
            f'project_id == "{self._quote(project_id)}" and revision_id == "{self._quote(revision_id)}"'
        )
        if module:
            expression += f' and module == "{self._quote(module)}"'
        if source_types:
            values = ", ".join(f'"{self._quote(value)}"' for value in source_types)
            expression += f" and source_type in [{values}]"
        result = self.client.search(
            collection_name=self.collection,
            data=[vector],
            filter=expression,
            limit=top_k,
            output_fields=["id"],
            search_params={"metric_type": "COSINE"},
        )
        return [VectorHit(id=str(hit["id"]), score=max(0.0, float(hit["distance"]))) for hit in result[0]]

    def delete_revision(self, project_id: str, revision_id: str) -> None:
        """集合存在时删除指定知识版本的全部向量。"""

        if not self.client.has_collection(collection_name=self.collection):
            return
        expression = (
            f'project_id == "{self._quote(project_id)}" and revision_id == "{self._quote(revision_id)}"'
        )
        self.client.delete(collection_name=self.collection, filter=expression)
