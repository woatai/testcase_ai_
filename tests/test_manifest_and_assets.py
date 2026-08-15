"""验证公开示例项目的 Manifest 复用能力和路径安全。"""

from __future__ import annotations

from pathlib import Path

import pytest

from testcase_ai.contracts import SourceType
from testcase_ai.manifest import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_business_packages_are_valid_and_reusable() -> None:
    ecommerce = load_manifest(ROOT / "projects" / "ecommerce-demo")
    assert ecommerce.id == "ecommerce-demo"
    assert any(source.type == SourceType.WORKFLOW for source in ecommerce.sources)


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    project = tmp_path / "unsafe"
    project.mkdir()
    (project / "project.yaml").write_text(
        """schema_version: "1"
id: unsafe
name: unsafe
version: "1"
sources:
  - type: business_rule
    path: ../outside
""",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="escapes project directory"):
        load_manifest(project)
