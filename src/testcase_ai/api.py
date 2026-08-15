"""FastAPI HTTP 入口；接口只负责协议适配，核心逻辑统一委托给服务层。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from testcase_ai.application import get_application
from testcase_ai.contracts import (
    ApprovalRequestV1,
    ApprovalResultV1,
    ClarificationContinuationV1,
    GenerationRequestV1,
    GenerationResultV1,
    KnowledgeSyncResult,
    RequirementInput,
    RetrievalRequestV1,
    RetrievedEvidence,
)
from testcase_ai.manifest import load_manifest, resolve_project
from testcase_ai.parsers import parse_requirement_file


@asynccontextmanager
async def lifespan(_: FastAPI):
    """在 API 开始接收请求前创建并初始化共享应用实例。"""

    get_application().initialize()
    yield


api = FastAPI(title="testcase_ai_", version="0.1.0", lifespan=lifespan)


def _bad_request(exc: Exception) -> HTTPException:
    """把服务层的可解释异常统一转换成 HTTP 400。"""

    return HTTPException(status_code=400, detail=str(exc))


@api.get("/health")
def health() -> dict[str, str]:
    """提供不触发模型调用的轻量存活检查。"""

    return {"status": "ok"}


@api.post("/api/v1/projects/{project_id}/validate")
def validate_project(project_id: str) -> dict:
    """校验业务包并返回标准化后的项目清单。"""

    application = get_application()
    try:
        project_dir, _ = resolve_project(application.settings.resolved_projects_root, project_id)
        return load_manifest(project_dir).model_dump(mode="json")
    except Exception as exc:
        raise _bad_request(exc) from exc


@api.post("/api/v1/projects/{project_id}/knowledge/sync", response_model=KnowledgeSyncResult)
def sync_knowledge(project_id: str) -> KnowledgeSyncResult:
    """同步指定项目的知识文件并返回发布版本信息。"""

    application = get_application()
    try:
        project_dir, _ = resolve_project(application.settings.resolved_projects_root, project_id)
        return application.knowledge.sync(project_dir)
    except Exception as exc:
        raise _bad_request(exc) from exc


@api.post("/api/v1/requirements", response_model=RequirementInput)
def normalize_requirement(requirement: RequirementInput) -> RequirementInput:
    """校验 JSON 需求并返回统一结构，供调用方在生成前预检查。"""

    return requirement


@api.post("/api/v1/requirements/upload", response_model=RequirementInput)
async def upload_requirement(file: Annotated[UploadFile, File()]) -> RequirementInput:
    """限制文件大小和类型后，将 Markdown、文本或 DOCX 解析成统一需求。"""

    application = get_application()
    content = await file.read(application.settings.max_upload_bytes + 1)
    if len(content) > application.settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="requirement file is too large")
    suffix = Path(file.filename or "requirement.md").suffix.lower()
    if suffix not in {".md", ".markdown", ".txt", ".docx"}:
        raise HTTPException(status_code=415, detail=f"unsupported requirement format: {suffix}")
    with NamedTemporaryFile(suffix=suffix) as temporary:
        temporary.write(content)
        temporary.flush()
        try:
            title, body = parse_requirement_file(Path(temporary.name))
        except Exception as exc:
            raise _bad_request(exc) from exc
    return RequirementInput(title=title, content=body, source_name=file.filename)


@api.post("/api/v1/retrieval/search", response_model=list[RetrievedEvidence])
def search(request: RetrievalRequestV1) -> list[RetrievedEvidence]:
    """执行带项目版本隔离的混合知识检索。"""

    try:
        return get_application().retrieval.search(request)
    except Exception as exc:
        raise _bad_request(exc) from exc


@api.post("/api/v1/testcase-generations", response_model=GenerationResultV1)
def generate(request: GenerationRequestV1) -> GenerationResultV1:
    """执行检索增强的测试用例生成，并同步返回完整结果。"""

    try:
        return get_application().generation.generate(request)
    except Exception as exc:
        raise _bad_request(exc) from exc


@api.get("/api/v1/testcase-generations/{generation_id}", response_model=GenerationResultV1)
def get_generation(generation_id: str) -> GenerationResultV1:
    """从数据库读取已完成的生成结果。"""

    try:
        return get_application().generation.get(generation_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/api/v1/testcase-generations/{generation_id}/continue", response_model=GenerationResultV1)
def continue_generation(generation_id: str, continuation: ClarificationContinuationV1) -> GenerationResultV1:
    """合并用户的澄清答案，以原任务为父节点重新执行生成。"""

    try:
        return get_application().generation.continue_generation(generation_id, continuation.answers)
    except Exception as exc:
        raise _bad_request(exc) from exc


@api.get("/api/v1/testcase-generations/{generation_id}/exports/{format}")
def download_export(generation_id: str, format: str) -> FileResponse:
    """下载一次生成任务已经产出的 JSON 或 XLSX 文件。"""

    if format not in {"json", "xlsx"}:
        raise HTTPException(status_code=404, detail="unsupported artifact format")
    try:
        result = get_application().generation.get(generation_id)
        path = Path(result.artifacts[format])
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, filename=path.name)


@api.post("/api/v1/testcase-generations/{generation_id}/approve", response_model=ApprovalResultV1)
def approve(generation_id: str, request: ApprovalRequestV1) -> ApprovalResultV1:
    """显式确认后，将选中草稿审批进项目正式用例库。"""

    try:
        return get_application().approval.approve(
            generation_id, request.selected_case_ids, confirm=request.confirm
        )
    except Exception as exc:
        raise _bad_request(exc) from exc
