"""验证生成预期与正式证据冲突时必须进入需求澄清状态。"""

from __future__ import annotations

from testcase_ai.contracts import Citation, RetrievedEvidence, SourceType
from testcase_ai.contracts import TestCaseV1 as CaseContract
from testcase_ai.quality import enforce_citation_grounding


def test_unsupported_expected_result_is_marked_for_clarification() -> None:
    evidence = RetrievedEvidence(
        evidence_id="e1",
        logical_id="l1",
        case_id="TC-RAG-0727",
        content="测试数据: -1,0,1\n预期结果: 提示大于0",
        source="订单.xlsx",
        source_type=SourceType.APPROVED_CASE,
        score=1,
    )
    testcase = CaseContract(
        case_id="DRAFT-001",
        applicable_versions=["标准版"],
        module="订单",
        test_path="售后管理",
        test_point="零金额退款",
        preconditions=["服务中订单"],
        test_data=["0"],
        steps=["提交退款"],
        expected_result="提示退款金额为0",
        citations=[
            Citation(
                evidence_id="e1",
                source="订单.xlsx",
                source_type=SourceType.APPROVED_CASE,
            )
        ],
    )
    checked, questions = enforce_citation_grounding([testcase], [evidence])
    assert checked[0].evidence_status == "needs_clarification"
    assert questions[0].affected_scope == "DRAFT-001"
