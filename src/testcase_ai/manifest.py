"""加载业务包的 ``project.yaml``，并安全地发现清单声明的知识文件。"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml

from testcase_ai.contracts import KnowledgeSourceConfig, ProjectManifestV1


class ManifestError(ValueError):
    """项目清单内容或路径不合法时抛出的业务异常。"""

    pass


def _safe_resolve(root: Path, relative: str) -> Path:
    """把相对路径限制在指定根目录内，阻止 ``../`` 等目录逃逸。"""

    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestError(f"path escapes project directory: {relative}") from exc
    return candidate


def load_manifest(project_dir: Path) -> ProjectManifestV1:
    """读取、校验 project.yaml，并确认清单 ID 与项目目录名一致。"""

    manifest_path = project_dir.resolve() / "project.yaml"
    if not manifest_path.is_file():
        raise ManifestError(f"project manifest not found: {manifest_path}")
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    manifest = ProjectManifestV1.model_validate(raw)
    if manifest.id != project_dir.name:
        raise ManifestError(f"manifest id '{manifest.id}' must match directory '{project_dir.name}'")
    validate_manifest_paths(project_dir, manifest)
    return manifest


def validate_manifest_paths(project_dir: Path, manifest: ProjectManifestV1) -> None:
    """确认所有知识源路径存在，且生成时要读取的策略文件都是普通文件。"""

    for source in manifest.sources:
        source_path = _safe_resolve(project_dir, source.path)
        if not source_path.exists():
            raise ManifestError(f"knowledge source does not exist: {source.path}")
    for policy in manifest.policies:
        policy_path = _safe_resolve(project_dir, policy)
        if not policy_path.is_file():
            raise ManifestError(f"policy file does not exist: {policy}")


def discover_source_files(project_dir: Path, source: KnowledgeSourceConfig) -> list[Path]:
    """按一项来源配置的 include/exclude 规则返回稳定排序的文件列表。"""

    source_root = _safe_resolve(project_dir, source.path)
    if source_root.is_file():
        return [source_root]

    files: set[Path] = set()
    for pattern in source.include:
        files.update(path for path in source_root.glob(pattern) if path.is_file())

    result: list[Path] = []
    for path in sorted(files):
        relative = path.relative_to(source_root).as_posix()
        if any(fnmatch.fnmatch(relative, pattern) for pattern in source.exclude):
            continue
        result.append(path)
    return result


def resolve_project(projects_root: Path, project_id: str) -> tuple[Path, ProjectManifestV1]:
    """在项目根目录中安全定位业务包，同时返回目录和已校验清单。"""

    project_dir = _safe_resolve(projects_root.resolve(), project_id)
    if not project_dir.is_dir():
        raise ManifestError(f"project not found: {project_id}")
    return project_dir, load_manifest(project_dir)
