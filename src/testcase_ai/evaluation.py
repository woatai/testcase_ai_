"""在固定 Gold Set 上评估检索 Recall、Hit Rate 与 MRR，并输出可读报告。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from testcase_ai.contracts import RetrievalRequestV1, SourceType
from testcase_ai.retrieval import RetrievalService


# 一条评测查询及其期望召回的正式用例编号。
class RetrievalGoldItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    query: str
    project_id: str
    relevant_case_ids: list[str] = Field(min_length=1)
    module: str | None = None
    source_types: list[SourceType] = Field(default_factory=lambda: [SourceType.APPROVED_CASE])
    notes: str | None = None


# 一份带版本和统一 TopK 的检索评测数据集。
class RetrievalGoldSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    top_k: int = Field(default=10, ge=1, le=100)
    items: list[RetrievalGoldItem] = Field(min_length=1)


# 单条查询的实际召回明细和指标。
class RetrievalEvaluationItem(BaseModel):
    id: str
    query: str
    relevant_case_ids: list[str]
    retrieved_case_ids: list[str]
    matched_case_ids: list[str]
    first_relevant_rank: int | None
    hit: bool
    recall: float


# 整个数据集的聚合指标、门禁结论和逐条结果。
class RetrievalEvaluationReport(BaseModel):
    dataset: str
    version: str
    top_k: int
    query_count: int
    hit_rate: float
    recall: float
    mrr: float
    passed: bool
    recall_threshold: float
    generated_at: datetime
    items: list[RetrievalEvaluationItem]


def load_gold_set(path: Path) -> RetrievalGoldSet:
    """读取 YAML Gold Set，并用严格 Pydantic 结构校验字段。"""

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RetrievalGoldSet.model_validate(payload)


class RetrievalEvaluator:
    """复用生产 RetrievalService 执行离线检索质量评测。"""

    def __init__(self, retrieval: RetrievalService) -> None:
        """注入待评估的检索服务。"""

        self.retrieval = retrieval

    def evaluate(
        self,
        gold_set: RetrievalGoldSet,
        *,
        recall_threshold: float = 0.80,
    ) -> RetrievalEvaluationReport:
        """逐条检索并计算平均 Recall、Hit Rate、MRR 及门槛是否通过。"""

        item_reports: list[RetrievalEvaluationItem] = []
        for item in gold_set.items:
            evidence = self.retrieval.search(
                RetrievalRequestV1(
                    project_id=item.project_id,
                    query=item.query,
                    module=item.module,
                    source_types=item.source_types,
                    top_k=gold_set.top_k,
                )
            )
            retrieved_ids = [entry.case_id for entry in evidence if entry.case_id]
            relevant = set(item.relevant_case_ids)
            matched = [case_id for case_id in retrieved_ids if case_id in relevant]
            first_rank = next(
                (rank for rank, case_id in enumerate(retrieved_ids, start=1) if case_id in relevant),
                None,
            )
            item_reports.append(
                RetrievalEvaluationItem(
                    id=item.id,
                    query=item.query,
                    relevant_case_ids=item.relevant_case_ids,
                    retrieved_case_ids=retrieved_ids,
                    matched_case_ids=list(dict.fromkeys(matched)),
                    first_relevant_rank=first_rank,
                    hit=first_rank is not None,
                    recall=len(set(matched)) / len(relevant),
                )
            )

        count = len(item_reports)
        hit_rate = sum(1 for item in item_reports if item.hit) / count
        recall = sum(item.recall for item in item_reports) / count
        mrr = sum(1 / item.first_relevant_rank for item in item_reports if item.first_relevant_rank) / count
        return RetrievalEvaluationReport(
            dataset=gold_set.name,
            version=gold_set.version,
            top_k=gold_set.top_k,
            query_count=count,
            hit_rate=hit_rate,
            recall=recall,
            mrr=mrr,
            passed=recall >= recall_threshold,
            recall_threshold=recall_threshold,
            generated_at=datetime.now(UTC),
            items=item_reports,
        )


def write_evaluation_report(report: RetrievalEvaluationReport, target_dir: Path) -> dict[str, str]:
    """同时写出机器可读 JSON 和包含失败明细的 Markdown 评测报告。"""

    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "retrieval-evaluation.json"
    markdown_path = target_dir / "retrieval-evaluation.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# {report.dataset} 检索评测",
        "",
        f"- 数据集版本：`{report.version}`",
        f"- 查询数：{report.query_count}",
        f"- HitRate@{report.top_k}：{report.hit_rate:.3f}",
        f"- Recall@{report.top_k}：{report.recall:.3f}",
        f"- MRR@{report.top_k}：{report.mrr:.3f}",
        f"- 通过门槛：Recall ≥ {report.recall_threshold:.2f}",
        f"- 结果：{'通过' if report.passed else '未通过'}",
        "",
        "| ID | 首个相关排名 | Recall | 命中正式用例 |",
        "| --- | ---: | ---: | --- |",
    ]
    for item in report.items:
        lines.append(
            f"| {item.id} | {item.first_relevant_rank or '-'} | {item.recall:.3f} | "
            f"{', '.join(item.matched_case_ids) or '-'} |"
        )
    failures = [item for item in report.items if not item.hit or item.recall < 1]
    if failures:
        lines.extend(["", "## 未完全召回项", ""])
        for item in failures:
            lines.append(
                f"- `{item.id}`：期望 {', '.join(item.relevant_case_ids)}；"
                f"Top{report.top_k} 返回 {', '.join(item.retrieved_case_ids) or '无正式用例'}。"
            )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}
