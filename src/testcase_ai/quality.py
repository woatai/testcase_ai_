"""生成后质量规则，当前重点校验生成结论是否被引用的正式用例支持。"""

from __future__ import annotations

import re

from testcase_ai.contracts import ClarificationQuestion, RetrievedEvidence, TestCaseV1


def _bigrams(text: str) -> set[str]:
    """把清洗后的中英文数字文本转换为二元字符集合，供轻量语义重叠计算使用。"""

    normalized = "".join(
        character.lower() for character in text if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def semantic_overlap(left: str, right: str) -> float:
    """以二元字符集合的 Jaccard 系数估算两段短文本的语义重叠度。"""

    left_terms = _bigrams(left)
    right_terms = _bigrams(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def _expected_result(content: str) -> str | None:
    """从标准正式用例知识块正文中抽取单行“预期结果”字段。"""

    matched = re.search(r"(?:^|\n)预期结果:\s*([^\n]+)", content)
    return matched.group(1).strip() if matched else None


def enforce_citation_grounding(
    cases: list[TestCaseV1],
    evidence: list[RetrievedEvidence],
    *,
    minimum_overlap: float = 0.45,
) -> tuple[list[TestCaseV1], list[ClarificationQuestion]]:
    """检查生成预期是否得到所引用正式用例的支持。

    只有引用 ``approved_case`` 的用例会进入检查。重叠度低于门槛时不会直接删除草稿，而是把
    ``evidence_status`` 改为 ``needs_clarification`` 并返回对应问题，阻止该用例直接审批。
    """

    evidence_by_id = {item.evidence_id: item for item in evidence}
    checked: list[TestCaseV1] = []
    questions: list[ClarificationQuestion] = []
    for case in cases:
        official_expectations = []
        for citation in case.citations:
            item = evidence_by_id.get(citation.evidence_id)
            if item and item.source_type.value == "approved_case":
                expected = _expected_result(item.content)
                if expected:
                    official_expectations.append(expected)
        if official_expectations:
            best_overlap = max(
                semantic_overlap(case.expected_result, expected) for expected in official_expectations
            )
            if best_overlap < minimum_overlap:
                case = case.model_copy(update={"evidence_status": "needs_clarification"})
                questions.append(
                    ClarificationQuestion(
                        id=f"grounding-{case.case_id}",
                        question=f"请确认“{case.test_point}”的正确预期结果。",
                        reason=(
                            f"生成预期“{case.expected_result}”与引用正式用例预期"
                            f"“{'；'.join(official_expectations)}”不一致。"
                        ),
                        affected_scope=case.case_id,
                    )
                )
        checked.append(case)
    return checked, questions
