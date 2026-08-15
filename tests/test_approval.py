"""验证审批必须显式确认、原子替换正式文件且重复调用保持幂等。"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from testcase_ai.approval import ApprovalService
from testcase_ai.contracts import (
    GenerationResultV1,
    GenerationStatus,
    KnowledgeSyncResult,
    RequirementInput,
)
from testcase_ai.contracts import (
    TestCaseV1 as CaseContract,
)
from testcase_ai.database import Database


class StubGeneration:
    def __init__(self, result: GenerationResultV1) -> None:
        self.result = result

    def get(self, generation_id: str) -> GenerationResultV1:
        assert generation_id == self.result.generation_id
        return self.result


class StubKnowledge:
    def sync(self, project_dir: Path) -> KnowledgeSyncResult:
        return KnowledgeSyncResult(
            project_id=project_dir.name,
            revision_id="revision-after-approval",
            source_count=1,
            chunk_count=2,
            indexed_count=2,
        )


def _write_project(projects: Path) -> Path:
    project = projects / "approval-demo"
    rules = project / "knowledge" / "rules"
    modules = project / "knowledge" / "approved-cases" / "modules"
    rules.mkdir(parents=True)
    modules.mkdir(parents=True)
    (rules / "规则.md").write_text("# 退款\n退款成功后返还金额。", encoding="utf-8")
    (project / "project.yaml").write_text(
        """schema_version: "1"
id: approval-demo
name: approval-demo
version: "1"
sources:
  - type: business_rule
    path: knowledge/rules
    include: ["**/*.md"]
  - type: approved_case
    path: knowledge/approved-cases/modules
    include: ["*.xlsx"]
excel:
  sheet: 全部用例
  header_row: 4
  data_start_row: 5
""",
        encoding="utf-8",
    )
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "全部用例"
    headers = [
        "用例编号",
        "适用版本",
        "功能模块",
        "测试路径",
        "测试点",
        "前置条件",
        "测试步骤",
        None,
        None,
        "测试数据",
        "预期结果",
        "实际结果",
        "通过 / 未通过",
        "测试日期",
    ]
    for index, header in enumerate(headers, start=1):
        worksheet.cell(row=4, column=index, value=header)
    worksheet.merge_cells("G4:I4")
    worksheet.append(
        [
            "TC-OLD-001",
            "标准版",
            "订单",
            "订单详情",
            "查看订单",
            "有订单",
            "打开详情",
            None,
            None,
            "订单",
            "显示订单",
            None,
            None,
            None,
        ]
    )
    worksheet.merge_cells("G5:I5")
    workbook.save(modules / "订单.xlsx")
    workbook.close()
    return project


def test_approval_is_explicit_atomic_and_idempotent(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = _write_project(projects)
    result = GenerationResultV1(
        generation_id="generation-1",
        project_id="approval-demo",
        project_version="1",
        knowledge_revision_id="revision-before",
        status=GenerationStatus.SUCCEEDED,
        requirement=RequirementInput(title="退款", content="订单退款"),
        cases=[
            CaseContract(
                case_id="DRAFT-001",
                applicable_versions=["标准版"],
                module="订单",
                test_path="售后管理",
                test_point="提交退款",
                preconditions=["订单已支付"],
                test_data=["退款100元"],
                steps=["提交退款"],
                expected_result="退款成功",
                evidence_status="inferred",
            )
        ],
    )
    database = Database(f"sqlite:///{tmp_path / 'approval.db'}")
    database.create_schema()
    service = ApprovalService(
        database=database,
        generation=StubGeneration(result),
        knowledge=StubKnowledge(),
        projects_root=projects,
        outputs_root=tmp_path / "outputs",
    )
    approved = service.approve("generation-1", ["DRAFT-001"], confirm=True)
    repeated = service.approve("generation-1", ["DRAFT-001"], confirm=True)
    assert approved.approval_id == repeated.approval_id
    assert approved.knowledge_revision_id == "revision-after-approval"
    assert len(list((tmp_path / "outputs").rglob("订单.xlsx"))) == 1

    workbook = load_workbook(project / "knowledge" / "approved-cases" / "modules" / "订单.xlsx")
    worksheet = workbook["全部用例"]
    rows = [row for row in worksheet.iter_rows(min_row=5, values_only=True) if any(row)]
    workbook.close()
    assert len(rows) == 2
    assert str(rows[-1][0]).startswith("AI-")
