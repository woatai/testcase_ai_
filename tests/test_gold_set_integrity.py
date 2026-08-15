"""确保公开仓库中的 JSON Schema 与 Python 数据契约一致。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_generated_json_contracts_are_current() -> None:
    import json

    from testcase_ai.contracts import (
        GenerationRequestV1,
        GenerationResultV1,
        ProjectManifestV1,
        TestCaseV1,
    )

    expected = {
        "project-manifest-v1.json": ProjectManifestV1,
        "testcase-v1.json": TestCaseV1,
        "generation-request-v1.json": GenerationRequestV1,
        "generation-result-v1.json": GenerationResultV1,
    }
    for filename, model in expected.items():
        actual = json.loads((ROOT / "contracts" / filename).read_text(encoding="utf-8"))
        assert actual == model.model_json_schema()
