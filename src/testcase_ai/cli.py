"""Typer 命令行入口，把终端参数转换为服务层数据契约。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from testcase_ai.application import get_application
from testcase_ai.contracts import GenerationRequestV1, RequirementInput, RetrievalRequestV1, SourceType
from testcase_ai.evaluation import RetrievalEvaluator, load_gold_set, write_evaluation_report
from testcase_ai.manifest import resolve_project
from testcase_ai.parsers import parse_requirement_file

app = typer.Typer(help="RAG 增强的 AI 测试用例生成平台")
project_app = typer.Typer(help="业务知识包操作")
knowledge_app = typer.Typer(help="知识索引操作")
eval_app = typer.Typer(help="RAG 检索评测")
app.add_typer(project_app, name="project")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(eval_app, name="eval")


@app.command()
def serve() -> None:
    """按配置的地址和端口启动本地 FastAPI 服务。"""
    import uvicorn

    settings = get_application().settings
    uvicorn.run("testcase_ai.api:api", host=settings.bind_host, port=settings.bind_port, reload=False)


@project_app.command("validate")
def validate_project(project: Annotated[str, typer.Option("--project")]) -> None:
    """加载并校验指定项目的 project.yaml、知识源路径和策略文件。"""

    application = get_application()
    project_dir, manifest = resolve_project(application.settings.resolved_projects_root, project)
    typer.echo(
        json.dumps(
            {"path": str(project_dir), **manifest.model_dump(mode="json")}, ensure_ascii=False, indent=2
        )
    )


@knowledge_app.command("sync")
def sync_knowledge(project: Annotated[str, typer.Option("--project")]) -> None:
    """解析项目知识文件，创建 Embedding，并发布新的 MySQL/Milvus 知识版本。"""

    application = get_application()
    project_dir, _ = resolve_project(application.settings.resolved_projects_root, project)
    result = application.knowledge.sync(project_dir)
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def search(
    project: Annotated[str, typer.Option("--project")],
    query: Annotated[str, typer.Option("--query")],
    module: Annotated[str | None, typer.Option("--module")] = None,
    source_type: Annotated[list[SourceType] | None, typer.Option("--source-type")] = None,
    top_k: Annotated[int, typer.Option("--top-k")] = 12,
) -> None:
    """执行一次可带模块和来源过滤的 Dense + BM25 + RRF 混合检索。"""

    result = get_application().retrieval.search(
        RetrievalRequestV1(
            project_id=project,
            query=query,
            module=module,
            source_types=source_type or [],
            top_k=top_k,
        )
    )
    typer.echo(json.dumps([item.model_dump(mode="json") for item in result], ensure_ascii=False, indent=2))


@app.command()
def generate(
    project: Annotated[str, typer.Option("--project")],
    requirement: Annotated[Path | None, typer.Option("--requirement", exists=True, dir_okay=False)] = None,
    text: Annotated[str | None, typer.Option("--text")] = None,
    title: Annotated[str | None, typer.Option("--title")] = None,
    module: Annotated[str | None, typer.Option("--module")] = None,
    output: Annotated[list[str] | None, typer.Option("--output")] = None,
) -> None:
    """从需求文件或文本构造生成请求，并同步返回测试用例及导出文件信息。"""

    if requirement:
        parsed_title, content = parse_requirement_file(requirement)
        requirement_input = RequirementInput(
            title=title or parsed_title,
            content=content,
            source_name=requirement.name,
        )
    elif text and title:
        requirement_input = RequirementInput(title=title, content=text)
    else:
        raise typer.BadParameter("provide --requirement, or provide both --title and --text")
    result = get_application().generation.generate(
        GenerationRequestV1(
            project_id=project,
            requirement=requirement_input,
            module_hint=module,
            output_formats=output or ["json", "xlsx"],
        )
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command()
def approve(
    generation_id: Annotated[str, typer.Option("--generation-id")],
    case_ids: Annotated[list[str], typer.Option("--case-id")],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """把用户明确选中的草稿用例写入正式 Excel；必须同时传入 ``--confirm``。"""

    result = get_application().approval.approve(generation_id, case_ids, confirm=confirm)
    typer.echo(result.model_dump_json(indent=2))


@eval_app.command("retrieval")
def evaluate_retrieval(
    dataset: Annotated[Path, typer.Option("--dataset", exists=True, dir_okay=False)],
    threshold: Annotated[float, typer.Option("--threshold", min=0, max=1)] = 0.80,
) -> None:
    """在 Gold Set 上运行检索评测，低于 Recall 门槛时以非零状态退出。"""

    application = get_application()
    gold_set = load_gold_set(dataset)
    report = RetrievalEvaluator(application.retrieval).evaluate(
        gold_set,
        recall_threshold=threshold,
    )
    target = application.settings.resolved_outputs_root / "evaluations" / gold_set.name / gold_set.version
    artifacts = write_evaluation_report(report, target)
    typer.echo(report.model_dump_json(indent=2))
    typer.echo(json.dumps({"artifacts": artifacts}, ensure_ascii=False, indent=2))
    if not report.passed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
