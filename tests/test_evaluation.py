"""验证检索评测的 Recall、Hit Rate 和 MRR 计算。"""

from __future__ import annotations

from testcase_ai.contracts import RetrievedEvidence, SourceType
from testcase_ai.evaluation import RetrievalEvaluator, RetrievalGoldItem, RetrievalGoldSet


class StubRetrieval:
    def search(self, request):
        return [
            RetrievedEvidence(
                evidence_id="e1",
                logical_id="l1",
                case_id="TC-001",
                content="退款后返还优惠券",
                source="订单.xlsx",
                source_type=SourceType.APPROVED_CASE,
                score=1,
            )
        ]


def test_evaluator_calculates_recall_hit_rate_and_mrr() -> None:
    gold_set = RetrievalGoldSet(
        name="demo",
        version="1",
        top_k=10,
        items=[
            RetrievalGoldItem(
                id="q1",
                query="退款优惠券",
                project_id="demo",
                relevant_case_ids=["TC-001"],
            )
        ],
    )
    report = RetrievalEvaluator(StubRetrieval()).evaluate(gold_set, recall_threshold=0.8)
    assert report.passed is True
    assert report.hit_rate == 1
    assert report.recall == 1
    assert report.mrr == 1
