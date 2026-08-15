"""测试共享夹具：提供确定性 Embedding 和可复用的临时业务知识包构造器。"""

from __future__ import annotations

import re
from pathlib import Path


class FakeEmbeddings:
    model = "fake-embedding"
    vocabulary = ["退款", "优惠券", "库存", "订单", "佣金", "技师", "门店", "预约"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            compact = re.sub(r"\s+", "", text)
            vector = [float(compact.count(term)) for term in self.vocabulary]
            vector.append(float(len(compact)) / 1000)
            vectors.append(vector)
        return vectors


def write_demo_project(root: Path, project_id: str, *, rule: str, approved_case: str) -> Path:
    project = root / project_id
    (project / "knowledge" / "rules").mkdir(parents=True)
    (project / "knowledge" / "approved").mkdir(parents=True)
    (project / "knowledge" / "rules" / "规则.md").write_text(rule, encoding="utf-8")
    (project / "knowledge" / "approved" / "正式用例.md").write_text(approved_case, encoding="utf-8")
    (project / "project.yaml").write_text(
        f"""schema_version: "1"
id: {project_id}
name: {project_id}
version: "1.0"
sources:
  - type: business_rule
    path: knowledge/rules
    include: ["**/*.md"]
  - type: approved_case
    path: knowledge/approved
    include: ["**/*.md"]
retrieval:
  dense_top_k: 10
  bm25_top_k: 10
  fused_top_k: 8
  rrf_k: 60
  query_rewrite_enabled: false
  rerank_enabled: false
excel:
  sheet: 全部用例
  header_row: 4
  data_start_row: 5
""",
        encoding="utf-8",
    )
    return project
