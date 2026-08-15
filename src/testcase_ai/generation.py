"""测试用例生成主链路：检索证据、调用大模型、质量校验、去重并导出草稿。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from testcase_ai.contracts import (
    Citation,
    ClarificationQuestion,
    GenerationRequestV1,
    GenerationResultV1,
    GenerationStatus,
    RetrievalRequestV1,
    TestCaseV1,
)
from testcase_ai.database import Database, GenerationRecord, KnowledgeChunkRecord, ProjectRecord
from testcase_ai.exporters import ArtifactExporter
from testcase_ai.manifest import resolve_project
from testcase_ai.providers import OpenAICompatibleGenerationProvider, ProviderError, parse_json_object
from testcase_ai.quality import enforce_citation_grounding
from testcase_ai.retrieval import RetrievalService


# 约束模型只能用本次 Prompt 中真实提供的 evidence_id。
class ModelCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str


# 面向大模型输出的严格结构；稍后会转换成对外 TestCaseV1。
class ModelTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    applicable_versions: list[str] = Field(min_length=1)
    module: str
    test_path: str
    test_point: str
    preconditions: list[str] = Field(min_length=1)
    test_data: list[str] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    expected_result: str
    coverage_tags: list[str] = Field(default_factory=list)
    citations: list[ModelCitation] = Field(default_factory=list)


# 单次模型响应同时容纳可生成用例和需要用户回答的澄清问题。
class ModelGenerationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[ModelTestCase] = Field(default_factory=list)
    clarifications: list[ClarificationQuestion] = Field(default_factory=list)


def _normalized_case_signature(case: TestCaseV1) -> str:
    """拼接用例关键字段并移除格式差异，生成精确重复检查使用的签名。"""

    value = "|".join(
        [
            case.module,
            case.test_path,
            case.test_point,
            *case.preconditions,
            *case.steps,
            case.expected_result,
        ]
    )
    return re.sub(r"\W+", "", value).lower()


class GenerationService:
    """协调检索、模型、质量规则、数据库和导出器完成一次同步生成。"""

    def __init__(
        self,
        *,
        database: Database,
        retrieval: RetrievalService,
        provider: OpenAICompatibleGenerationProvider,
        exporter: ArtifactExporter,
        projects_root: Path,
        platform_prompt: Path,
    ) -> None:
        """注入生成链路依赖，并将项目根目录和平台 Prompt 固定为绝对路径。"""

        self.database = database
        self.retrieval = retrieval
        self.provider = provider
        self.exporter = exporter
        self.projects_root = projects_root.resolve()
        self.platform_prompt = platform_prompt.resolve()

    def generate(self, request: GenerationRequestV1) -> GenerationResultV1:
        """执行一次完整的 RAG 测试用例生成。

        生成任务固定使用开始时的 active revision，避免同步知识时证据漂移。需求标题、模块和
        正文先用于混合检索；检索证据、项目规则及澄清答案共同组成 Prompt。模型结果依次经过
        Schema 修复、证据引用落地、引用一致性检查和正式用例精确去重，最后导出并持久化。
        任意业务或 Provider 异常都会转换成带错误信息的 FAILED 结果，而不是丢失任务记录。
        """

        project_dir, manifest = resolve_project(self.projects_root, request.project_id)
        with self.database.session() as session:
            project = session.get(ProjectRecord, request.project_id)
            if project is None or not project.active_revision_id:
                raise ValueError(f"project has no active knowledge revision: {request.project_id}")
            revision_id = project.active_revision_id

        generation_id = str(uuid.uuid4())
        with self.database.session() as session:
            session.add(
                GenerationRecord(
                    id=generation_id,
                    project_id=request.project_id,
                    revision_id=revision_id,
                    parent_generation_id=request.parent_generation_id,
                    status=GenerationStatus.RUNNING.value,
                    request_json=request.model_dump(mode="json"),
                    model_name=self.provider.model,
                )
            )

        query = "\n".join(
            part
            for part in [request.requirement.title, request.module_hint or "", request.requirement.content]
            if part
        )
        evidence = self.retrieval.search(
            RetrievalRequestV1(
                project_id=request.project_id,
                query=query,
                module=request.module_hint,
                top_k=manifest.retrieval.fused_top_k,
            )
        )
        if not evidence:
            return self._fail(generation_id, request, manifest.version, revision_id, "no retrieval evidence")

        project_policies = "\n\n".join(
            (project_dir / relative).read_text(encoding="utf-8") for relative in manifest.policies
        )
        evidence_text = "\n\n".join(
            (
                f"[evidence_id={item.evidence_id}]\n"
                f"来源: {item.source}\n章节: {item.section or '-'}\n{item.content}"
            )
            for item in evidence
        )
        prompt = self.platform_prompt.read_text(encoding="utf-8")
        user_content = (
            f"项目规则：\n{project_policies}\n\n"
            f"需求标题：{request.requirement.title}\n需求内容：\n{request.requirement.content}\n\n"
            f"模块提示：{request.module_hint or '无'}\n"
            f"澄清答案：{json.dumps(request.clarification_answers, ensure_ascii=False)}\n\n"
            f"检索证据：\n{evidence_text}"
        )
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user_content}]
        prompt_hash = hashlib.sha256((prompt + user_content).encode("utf-8")).hexdigest()

        try:
            payload = self._generate_payload(messages)
            cases = self._materialize_cases(payload.cases, evidence)
            cases, grounding_questions = enforce_citation_grounding(cases, evidence)
            cases, skipped = self._remove_exact_duplicates(cases, request.project_id, revision_id)
            clarifications = [*payload.clarifications, *grounding_questions]
            status = GenerationStatus.NEEDS_CLARIFICATION if clarifications else GenerationStatus.SUCCEEDED
            result = GenerationResultV1(
                generation_id=generation_id,
                project_id=request.project_id,
                project_version=manifest.version,
                knowledge_revision_id=revision_id,
                status=status,
                requirement=request.requirement,
                cases=cases,
                evidence=evidence,
                clarifications=clarifications,
                skipped_duplicates=skipped,
                model_profile=self.provider.model,
                prompt_hash=prompt_hash,
            )
            result.artifacts = self.exporter.export(result, request.output_formats)
            with self.database.session() as session:
                record = session.get(GenerationRecord, generation_id)
                if record:
                    record.status = status.value
                    record.result_json = result.model_dump(mode="json")
                    record.prompt_hash = prompt_hash
                    record.completed_at = datetime.now(UTC)
            return result
        except Exception as exc:
            return self._fail(generation_id, request, manifest.version, revision_id, str(exc), prompt_hash)

    def _generate_payload(self, messages: list[dict[str, str]]) -> ModelGenerationPayload:
        """调用模型并校验严格 Schema；首次格式不合格时仅允许自动修复一次。"""

        schema = ModelGenerationPayload.model_json_schema()
        raw = self.provider.complete_json(messages=messages, schema=schema)
        try:
            return ModelGenerationPayload.model_validate(parse_json_object(raw))
        except (ProviderError, ValidationError) as first_error:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": f"上次输出不符合 JSON Schema：{first_error}。仅返回修复后的 JSON。",
                },
            ]
            repaired = self.provider.complete_json(messages=repair_messages, schema=schema)
            return ModelGenerationPayload.model_validate(parse_json_object(repaired))

    @staticmethod
    def _materialize_cases(model_cases: list[ModelTestCase], evidence: list) -> list[TestCaseV1]:
        """把模型结构转换成平台用例，只保留本次检索结果中真实存在的证据引用。"""

        evidence_by_id = {item.evidence_id: item for item in evidence}
        cases: list[TestCaseV1] = []
        for index, item in enumerate(model_cases, start=1):
            citations = []
            for model_citation in item.citations:
                matched = evidence_by_id.get(model_citation.evidence_id)
                if matched:
                    citations.append(
                        Citation(
                            evidence_id=matched.evidence_id,
                            source=matched.source,
                            section=matched.section,
                            source_type=matched.source_type,
                        )
                    )
            case_id = item.case_id if item.case_id.startswith("DRAFT-") else f"DRAFT-{index:03d}"
            cases.append(
                TestCaseV1(
                    case_id=case_id,
                    applicable_versions=item.applicable_versions,
                    module=item.module,
                    test_path=item.test_path,
                    test_point=item.test_point,
                    preconditions=item.preconditions,
                    test_data=item.test_data,
                    steps=item.steps,
                    expected_result=item.expected_result,
                    coverage_tags=item.coverage_tags,
                    citations=citations,
                    evidence_status="cited" if citations else "inferred",
                )
            )
        return cases

    def _remove_exact_duplicates(
        self, cases: list[TestCaseV1], project_id: str, revision_id: str
    ) -> tuple[list[TestCaseV1], list[dict[str, str]]]:
        """与当前版本正式用例比较归一化签名，分离保留用例和重复跳过记录。"""

        with self.database.session() as session:
            approved = list(
                session.scalars(
                    select(KnowledgeChunkRecord).where(
                        KnowledgeChunkRecord.project_id == project_id,
                        KnowledgeChunkRecord.revision_id == revision_id,
                        KnowledgeChunkRecord.source_type == "approved_case",
                    )
                )
            )
        signatures: dict[str, str] = {}
        for chunk in approved:
            fields = chunk.metadata_json.get("fields") or {}
            pseudo = TestCaseV1(
                case_id=fields.get("用例编号") or chunk.logical_id,
                applicable_versions=[fields.get("适用版本") or "未知"],
                module=fields.get("功能模块") or chunk.module or "未知",
                test_path=fields.get("测试路径") or "未知",
                test_point=fields.get("测试点") or "未知",
                preconditions=[fields.get("前置条件") or "无"],
                test_data=[fields.get("测试数据") or "无"],
                steps=[fields.get("测试步骤") or "未知"],
                expected_result=fields.get("预期结果") or "未知",
                evidence_status="inferred",
            )
            signatures[_normalized_case_signature(pseudo)] = pseudo.case_id
        kept: list[TestCaseV1] = []
        skipped: list[dict[str, str]] = []
        for case in cases:
            duplicate = signatures.get(_normalized_case_signature(case))
            if duplicate:
                skipped.append({"case_id": case.case_id, "duplicate_of": duplicate, "reason": "exact"})
            else:
                kept.append(case)
        return kept, skipped

    def _fail(
        self,
        generation_id: str,
        request: GenerationRequestV1,
        project_version: str,
        revision_id: str,
        error: str,
        prompt_hash: str | None = None,
    ) -> GenerationResultV1:
        """构造 FAILED 结果并回写任务记录，截断过长错误以适配数据库字段。"""

        result = GenerationResultV1(
            generation_id=generation_id,
            project_id=request.project_id,
            project_version=project_version,
            knowledge_revision_id=revision_id,
            status=GenerationStatus.FAILED,
            requirement=request.requirement,
            prompt_hash=prompt_hash,
            error=error[:2000],
        )
        with self.database.session() as session:
            record = session.get(GenerationRecord, generation_id)
            if record:
                record.status = GenerationStatus.FAILED.value
                record.result_json = result.model_dump(mode="json")
                record.error = result.error
                record.completed_at = datetime.now(UTC)
        return result

    def get(self, generation_id: str) -> GenerationResultV1:
        """按 ID 读取已落库的完整生成结果，不存在或尚无结果时抛出业务异常。"""

        with self.database.session() as session:
            record = session.get(GenerationRecord, generation_id)
            if record is None or record.result_json is None:
                raise ValueError(f"generation result not found: {generation_id}")
            return GenerationResultV1.model_validate(record.result_json)

    def continue_generation(self, generation_id: str, answers: dict[str, str]) -> GenerationResultV1:
        """读取原请求、累计澄清答案并创建一条指向原任务的新生成记录。"""

        with self.database.session() as session:
            record = session.get(GenerationRecord, generation_id)
            if record is None:
                raise ValueError(f"generation not found: {generation_id}")
            request = GenerationRequestV1.model_validate(record.request_json)
        request.parent_generation_id = generation_id
        request.clarification_answers = {**request.clarification_answers, **answers}
        return self.generate(request)
