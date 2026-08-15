"""从 Pydantic 数据契约重新生成供外部系统使用的 ``contracts/*.json`` Schema。"""

from __future__ import annotations

import json
from pathlib import Path

from testcase_ai.contracts import GenerationRequestV1, GenerationResultV1, ProjectManifestV1, TestCaseV1

CONTRACTS = {
    "project-manifest-v1.json": ProjectManifestV1,
    "testcase-v1.json": TestCaseV1,
    "generation-request-v1.json": GenerationRequestV1,
    "generation-result-v1.json": GenerationResultV1,
}


def main() -> None:
    """导出四个公开 V1 模型；同名 Schema 文件会被当前模型定义覆盖。"""

    target = Path("contracts")
    target.mkdir(parents=True, exist_ok=True)
    for filename, model in CONTRACTS.items():
        (target / filename).write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
