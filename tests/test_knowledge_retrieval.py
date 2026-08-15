"""验证知识同步增量性、项目隔离和混合检索结果。"""

from __future__ import annotations

from pathlib import Path

from conftest import FakeEmbeddings, write_demo_project

from testcase_ai.contracts import RetrievalRequestV1
from testcase_ai.database import Database
from testcase_ai.knowledge import KnowledgeService
from testcase_ai.retrieval import RetrievalService
from testcase_ai.vector_store import InMemoryVectorStore


def test_sync_is_incremental_and_projects_are_isolated(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    home = write_demo_project(
        projects,
        "home-demo",
        rule="# 退款\n订单退款后返还优惠券。",
        approved_case="# TC-HOME-001\n订单退款后优惠券回到账户。",
    )
    commerce = write_demo_project(
        projects,
        "commerce-demo",
        rule="# 库存\n订单超时释放库存。",
        approved_case="# TC-EC-001\n未支付订单关闭后恢复库存。",
    )
    database = Database(f"sqlite:///{tmp_path / 'platform.db'}")
    database.create_schema()
    embeddings = FakeEmbeddings()
    vectors = InMemoryVectorStore()
    knowledge = KnowledgeService(database=database, embeddings=embeddings, vector_store=vectors)
    retrieval = RetrievalService(database=database, embeddings=embeddings, vector_store=vectors)

    first = knowledge.sync(home)
    unchanged = knowledge.sync(home)
    knowledge.sync(commerce)
    assert first.unchanged is False
    assert unchanged.unchanged is True
    assert unchanged.revision_id == first.revision_id

    home_hits = retrieval.search(
        RetrievalRequestV1(project_id="home-demo", query="退款以后优惠券怎么办", top_k=5)
    )
    assert home_hits
    assert all("库存" not in hit.content for hit in home_hits)

    rule_file = home / "knowledge" / "rules" / "规则.md"
    rule_file.write_text("# 退款\n订单退款后返还优惠券，并记录退款流水。", encoding="utf-8")
    changed = knowledge.sync(home)
    assert changed.unchanged is False
    assert changed.revision_id != first.revision_id
