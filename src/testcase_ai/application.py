"""应用装配入口：把配置、基础设施适配器和业务服务连接成同一个应用对象。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from testcase_ai.approval import ApprovalService
from testcase_ai.config import Settings, get_settings
from testcase_ai.database import Database
from testcase_ai.exporters import ArtifactExporter
from testcase_ai.generation import GenerationService
from testcase_ai.knowledge import KnowledgeService
from testcase_ai.providers import OpenAICompatibleEmbeddingProvider, OpenAICompatibleGenerationProvider
from testcase_ai.retrieval import RetrievalService
from testcase_ai.vector_store import MilvusVectorStore


class Application:
    """保存平台所有长生命周期服务，是 CLI 与 HTTP API 共用的依赖容器。"""

    def __init__(self, settings: Settings) -> None:
        """根据配置创建数据库、模型、向量库以及知识/检索/生成/审批服务。

        构造阶段只组装对象；Milvus 和模型 HTTP 请求会在实际同步、检索或生成时发生。
        """

        self.settings = settings
        self.database = Database(settings.database_url)
        self.embeddings = OpenAICompatibleEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value(),
            model=settings.embedding_model,
        )
        self.generator_provider = OpenAICompatibleGenerationProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
        )
        self.vector_store = MilvusVectorStore(
            uri=settings.milvus_uri,
            collection=settings.milvus_collection,
            token=settings.milvus_token.get_secret_value() if settings.milvus_token else None,
        )
        self.knowledge = KnowledgeService(
            database=self.database,
            embeddings=self.embeddings,
            vector_store=self.vector_store,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        self.retrieval = RetrievalService(
            database=self.database,
            embeddings=self.embeddings,
            vector_store=self.vector_store,
            projects_root=settings.resolved_projects_root,
        )
        self.generation = GenerationService(
            database=self.database,
            retrieval=self.retrieval,
            provider=self.generator_provider,
            exporter=ArtifactExporter(settings.resolved_outputs_root),
            projects_root=settings.resolved_projects_root,
            platform_prompt=Path("prompts/testcase-generation.md"),
        )
        self.approval = ApprovalService(
            database=self.database,
            generation=self.generation,
            knowledge=self.knowledge,
            projects_root=settings.resolved_projects_root,
            outputs_root=settings.resolved_outputs_root,
        )

    def initialize(self) -> None:
        """确保数据库基础表存在，使 CLI 和 API 在首次启动时具备最小可运行结构。"""

        self.database.create_schema()


@lru_cache(maxsize=1)
def get_application() -> Application:
    """返回进程内唯一的应用实例，并在首次访问时初始化数据库表。"""

    application = Application(get_settings())
    application.initialize()
    return application
