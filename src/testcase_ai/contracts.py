"""定义 CLI、HTTP API 与服务层共用的数据契约。

这些 Pydantic 模型是字段结构的唯一事实来源；``scripts/export_contracts.py`` 会据此导出 JSON Schema。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# 知识来源类型会写入 MySQL 和 Milvus，并可作为检索过滤条件。
class SourceType(StrEnum):
    POLICY = "policy"
    BUSINESS_RULE = "business_rule"
    WORKFLOW = "workflow"
    REQUIREMENT = "requirement"
    API_SPEC = "api_spec"
    GLOSSARY = "glossary"
    APPROVED_CASE = "approved_case"


# project.yaml 中一项知识来源的路径和文件匹配规则。
class KnowledgeSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SourceType
    path: str
    include: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude: list[str] = Field(default_factory=list)


# 每个业务知识包独立配置的混合检索参数。
class RetrievalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dense_top_k: int = Field(default=20, ge=1, le=200)
    bm25_top_k: int = Field(default=20, ge=1, le=200)
    fused_top_k: int = Field(default=12, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    query_rewrite_enabled: bool = False
    rerank_enabled: bool = False


# 正式用例 Excel 的工作表及表头位置约定。
class ExcelMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet: str = "全部用例"
    header_row: int = Field(default=4, ge=1)
    data_start_row: int = Field(default=5, ge=2)


# project.yaml 的完整结构；禁止未声明字段，防止配置拼写错误被静默忽略。
class ProjectManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=64)
    language: str = "zh-CN"
    sources: list[KnowledgeSourceConfig] = Field(min_length=1)
    policies: list[str] = Field(default_factory=list)
    retrieval: RetrievalProfile = Field(default_factory=RetrievalProfile)
    excel: ExcelMapping = Field(default_factory=ExcelMapping)


# 一条生成用例对检索证据的引用。
class Citation(BaseModel):
    evidence_id: str
    source: str
    section: str | None = None
    source_type: SourceType | None = None


# 平台内部、导出文件和审批流程共用的标准测试用例结构。
class TestCaseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    applicable_versions: list[str] = Field(min_length=1)
    module: str = Field(min_length=1)
    test_path: str = Field(min_length=1)
    test_point: str = Field(min_length=1)
    preconditions: list[str] = Field(min_length=1)
    test_data: list[str] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    actual_result: str | None = None
    execution_status: Literal["通过", "未通过"] | None = None
    test_date: date | None = None
    coverage_tags: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence_status: Literal["cited", "inferred", "needs_clarification"] = "cited"
    duplicate_of: str | None = None
    duplicate_score: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_evidence(self) -> TestCaseV1:
        """保证声明为“有引用”的用例确实携带至少一条证据引用。"""

        if self.evidence_status == "cited" and not self.citations:
            raise ValueError("cited testcase must include at least one citation")
        return self


# 经过文本或文件解析后得到的统一需求输入。
class RequirementInput(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_name: str | None = None


# 发起一次测试用例生成所需的完整参数。
class GenerationRequestV1(BaseModel):
    project_id: str
    requirement: RequirementInput
    module_hint: str | None = None
    clarification_answers: dict[str, str] = Field(default_factory=dict)
    parent_generation_id: str | None = None
    output_formats: list[Literal["json", "xlsx"]] = Field(default_factory=lambda: ["json", "xlsx"])


# 证据不足或证据冲突时返回给用户的澄清问题。
class ClarificationQuestion(BaseModel):
    id: str
    question: str
    reason: str
    affected_scope: str | None = None


# 混合检索返回的证据，同时保留 Dense/BM25 排名以便排查效果。
class RetrievedEvidence(BaseModel):
    evidence_id: str
    logical_id: str
    case_id: str | None = None
    content: str
    source: str
    source_type: SourceType
    section: str | None = None
    module: str | None = None
    score: float = Field(ge=0)
    dense_rank: int | None = None
    sparse_rank: int | None = None


# 生成任务的生命周期状态。
class GenerationStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"


# 一次生成的完整结果，包含用例、证据、澄清项和导出文件路径。
class GenerationResultV1(BaseModel):
    generation_id: str
    project_id: str
    project_version: str
    knowledge_revision_id: str
    status: GenerationStatus
    requirement: RequirementInput
    cases: list[TestCaseV1] = Field(default_factory=list)
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    clarifications: list[ClarificationQuestion] = Field(default_factory=list)
    skipped_duplicates: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    model_profile: str | None = None
    prompt_hash: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None


# 独立执行知识检索时使用的请求参数。
class RetrievalRequestV1(BaseModel):
    project_id: str
    query: str = Field(min_length=1)
    module: str | None = None
    source_types: list[SourceType] = Field(default_factory=list)
    top_k: int | None = Field(default=None, ge=1, le=100)


# 知识同步完成后的版本与索引统计。
class KnowledgeSyncResult(BaseModel):
    project_id: str
    revision_id: str
    source_count: int
    chunk_count: int
    indexed_count: int
    unchanged: bool = False


# 人工审批请求；confirm 必须显式为真才允许改正式资产。
class ApprovalRequestV1(BaseModel):
    selected_case_ids: list[str] = Field(min_length=1)
    confirm: bool = False


# 审批入库结果及新知识版本号。
class ApprovalResultV1(BaseModel):
    approval_id: str
    generation_id: str
    project_id: str
    imported_case_ids: list[str]
    target_files: list[str]
    knowledge_revision_id: str | None = None


# 用户回答澄清问题后继续生成时提交的答案。
class ClarificationContinuationV1(BaseModel):
    answers: dict[str, str] = Field(min_length=1)
