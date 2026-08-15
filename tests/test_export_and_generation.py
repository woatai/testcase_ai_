"""验证生成服务使用检索证据并导出兼容结构的 JSON 与 Excel。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import FakeEmbeddings, write_demo_project
from openpyxl import load_workbook

from testcase_ai.contracts import GenerationRequestV1, GenerationStatus, RequirementInput
from testcase_ai.database import Database
from testcase_ai.exporters import ArtifactExporter
from testcase_ai.generation import GenerationService
from testcase_ai.knowledge import KnowledgeService
from testcase_ai.retrieval import RetrievalService
from testcase_ai.vector_store import InMemoryVectorStore


class FakeGenerationProvider:
    model = "fake-generation"

    def complete_json(self, *, messages: list[dict[str, str]], schema: dict) -> str:
        evidence_id = re.search(r"evidence_id=([a-f0-9-]+)", messages[1]["content"]).group(1)
        return json.dumps(
            {
                "cases": [
                    {
                        "case_id": "DRAFT-001",
                        "applicable_versions": ["标准版"],
                        "module": "订单",
                        "test_path": "用户端/订单详情",
                        "test_point": "退款后返还优惠券",
                        "preconditions": ["订单已支付并使用优惠券"],
                        "test_data": ["有效优惠券"],
                        "steps": ["提交订单退款申请"],
                        "expected_result": "退款成功且优惠券返还",
                        "coverage_tags": ["主流程"],
                        "citations": [{"evidence_id": evidence_id}],
                    }
                ],
                "clarifications": [],
            },
            ensure_ascii=False,
        )


def test_generation_uses_retrieval_and_exports_compatible_excel(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = write_demo_project(
        projects,
        "home-demo",
        rule="# 退款规则\n订单退款成功后返还已使用优惠券。",
        approved_case="# 历史用例\n退款完成后优惠券回到账户。",
    )
    database = Database(f"sqlite:///{tmp_path / 'platform.db'}")
    database.create_schema()
    embeddings = FakeEmbeddings()
    vectors = InMemoryVectorStore()
    knowledge = KnowledgeService(database=database, embeddings=embeddings, vector_store=vectors)
    retrieval = RetrievalService(database=database, embeddings=embeddings, vector_store=vectors)
    knowledge.sync(project)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("只返回符合 Schema 的 JSON，并引用证据。", encoding="utf-8")
    generation = GenerationService(
        database=database,
        retrieval=retrieval,
        provider=FakeGenerationProvider(),
        exporter=ArtifactExporter(tmp_path / "outputs"),
        projects_root=projects,
        platform_prompt=prompt,
    )
    result = generation.generate(
        GenerationRequestV1(
            project_id="home-demo",
            requirement=RequirementInput(title="优惠券退款", content="订单退款后返还优惠券"),
        )
    )
    assert result.status == GenerationStatus.SUCCEEDED
    assert len(result.cases) == 1
    assert result.cases[0].citations
    assert set(result.artifacts) == {"json", "xlsx"}

    workbook = load_workbook(result.artifacts["xlsx"], read_only=False, data_only=True)
    worksheet = workbook["全部用例"]
    assert worksheet.freeze_panes == "A5"
    assert worksheet["A4"].value == "用例编号"
    assert worksheet["G4"].value == "测试步骤"
    assert "G4:I4" in {str(item) for item in worksheet.merged_cells.ranges}
    assert workbook["来源映射"]["B2"].value == result.cases[0].citations[0].evidence_id
    workbook.close()
