"""集中定义运行配置，并从项目根目录的 ``.env`` 或系统环境变量读取配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """平台运行参数。

    所有环境变量都使用 ``TESTCASE_AI_`` 前缀；字段默认值面向本地 Docker + Ollama 环境。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TESTCASE_AI_",
        extra="ignore",
    )

    database_url: str = "mysql+pymysql://testcase:testcase@127.0.0.1:3306/testcase_ai"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr | None = None
    milvus_collection: str = "testcase_ai_chunks"

    projects_root: Path = Path("projects")
    outputs_root: Path = Path("outputs")

    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: SecretStr = SecretStr("ollama")
    llm_model: str = ""
    embedding_base_url: str = "http://127.0.0.1:11434/v1"
    embedding_api_key: SecretStr = SecretStr("ollama")
    embedding_model: str = ""

    dense_top_k: int = Field(default=20, ge=1, le=200)
    bm25_top_k: int = Field(default=20, ge=1, le=200)
    fused_top_k: int = Field(default=12, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    duplicate_similarity_threshold: float = Field(default=0.90, ge=0, le=1)
    chunk_size: int = Field(default=900, ge=200, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=1000)

    query_rewrite_enabled: bool = False
    rerank_enabled: bool = False
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8000, ge=1, le=65535)
    max_upload_bytes: int = 20 * 1024 * 1024

    @property
    def resolved_projects_root(self) -> Path:
        """返回业务知识包根目录的绝对路径，避免后续路径校验受工作目录变化影响。"""

        return self.projects_root.resolve()

    @property
    def resolved_outputs_root(self) -> Path:
        """返回生成物根目录的绝对路径。"""

        return self.outputs_root.resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """创建并缓存全局配置，保证同一进程内读取到一致的环境参数。"""

    return Settings()
