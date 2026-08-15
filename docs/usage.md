# 完整使用指南

这份指南面向第一次在新电脑上使用本项目的人，覆盖安装、配置、启动、知识同步、检索、生成、API、私有业务包和排错。

## 0. 为什么 GitHub 和本地目录不完全一样

这是正常现象。Git 只同步应该共享的源文件，不会把每台电脑的全部运行状态上传：

- GitHub 会包含代码、迁移、依赖锁文件、公开文档、编辑器项目设置和虚构演示知识包；
- 新电脑执行 `uv sync` 后才会生成 `.venv`、Python 缓存和测试缓存；
- 执行生成、评测后才会出现 `outputs/` 和临时文件；
- `.env` 可能含密钥，只从 `.env.example` 本地复制，不上传；
- 真实业务知识、正式用例、Gold Set 和历史资料必须通过安全渠道单独迁移，不上传公开 GitHub。

所以目标不是让两个目录逐文件完全相同，而是让任意新克隆都能按本文从零恢复出可运行环境，并能用公开演示包跑通主链路。

## 1. 你会启动什么

项目没有单独的前端。主链路由以下进程组成：

```text
CLI 或 FastAPI
  ├─ MySQL：保存知识版本、生成任务和审批记录
  ├─ Milvus：保存向量索引
  │   ├─ etcd：Milvus 元数据
  │   └─ MinIO：Milvus 对象存储
  └─ OpenAI-compatible 模型服务
      ├─ Embedding 模型：知识同步和 Dense 检索
      └─ Chat 模型：生成测试用例
```

本地推荐使用 Docker Compose 启动 MySQL、Milvus、etcd 和 MinIO，使用 Ollama 提供 Embedding 与 Chat 接口。`uv run testcase-ai serve` 只启动 FastAPI；CLI 命令和 API 共用同一套服务层。

## 2. 新电脑首次安装

### 2.1 前置软件

- Git；
- Python 3.11 或更高版本；仓库的 `.python-version` 指定 Python 3.12；
- [uv](https://docs.astral.sh/uv/getting-started/installation/)；
- [Docker Compose](https://docs.docker.com/compose/install/)；
- [Ollama](https://docs.ollama.com/quickstart)，或其他兼容 OpenAI API 的模型服务。

先确认命令可用：

```bash
git --version
uv --version
docker compose version
ollama --version
```

### 2.2 克隆并安装依赖

```bash
git clone https://github.com/woatai/testcase_ai_.git
cd testcase_ai_
uv sync --extra dev
cp .env.example .env
```

`uv sync` 会根据 `pyproject.toml` 和 `uv.lock` 创建项目根目录下的 `.venv`，不需要手工执行 `python -m venv` 或 `pip install`。

如果使用 Cursor/VS Code，请打开整个仓库目录，不要只打开 `src`。仓库中的 `.vscode/settings.json` 会选择 `${workspaceFolder}/.venv/bin/python`，并把 `src` 加入分析路径。如果界面没有立即刷新，执行 `Developer: Reload Window`，再运行 `Python: Select Interpreter` 确认选中 `.venv/bin/python`。

Windows 上 `uv` 同样会创建 `.venv`，但解释器实际位于 `.venv\\Scripts\\python.exe`。若编辑器没有自动识别，请手工选择该路径；命令行仍统一使用 `uv run ...`。

### 2.3 准备本地模型

默认 `.env.example` 使用以下 Ollama 模型：

```bash
ollama pull qwen3:1.7b
ollama pull bge-m3
ollama list
```

复制出的 `.env` 已包含：

```dotenv
TESTCASE_AI_LLM_BASE_URL=http://127.0.0.1:11434/v1
TESTCASE_AI_LLM_API_KEY=ollama
TESTCASE_AI_LLM_MODEL=qwen3:1.7b
TESTCASE_AI_EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
TESTCASE_AI_EMBEDDING_API_KEY=ollama
TESTCASE_AI_EMBEDDING_MODEL=bge-m3:latest
```

若 Ollama 没有作为系统服务运行，另开终端执行：

```bash
ollama serve
```

使用外部 OpenAI-compatible 服务时，修改 `.env` 中两组 `BASE_URL`、`API_KEY` 和 `MODEL`。生成与 Embedding 可以使用不同服务。不要提交 `.env`，它已被 `.gitignore` 排除。

### 2.4 启动数据服务

```bash
docker compose up -d mysql etcd minio milvus
docker compose ps
```

等待 `mysql` 和 `milvus` 显示为 healthy 后初始化数据库：

```bash
uv run alembic upgrade head
```

数据存放在 Docker volumes 中，普通的 `docker compose down` 不会删除数据。不要在仍需这些数据时执行 `docker compose down -v`。

## 3. 用公开演示包跑通主链路

`projects/ecommerce-demo` 是完全虚构的知识包，包含库存、优惠券、下单 Workflow 和示例正式用例，可安全提交到公开仓库。

### 3.1 校验业务包

```bash
uv run testcase-ai project validate --project ecommerce-demo
```

这个命令检查 `project.yaml` 的字段、项目 ID、知识路径和策略文件，不连接模型。

### 3.2 建立知识索引

```bash
uv run testcase-ai knowledge sync --project ecommerce-demo
```

同步过程会解析知识文件、调用 Embedding 模型、把版本元数据写入 MySQL，并把向量写入 Milvus。成功结果包含 `revision_id`、文件数和分块数。内容未变化时再次同步会返回 `"unchanged": true`。

### 3.3 检索知识

```bash
uv run testcase-ai search \
  --project ecommerce-demo \
  --query "库存不足时能否提交订单" \
  --source-type business_rule \
  --top-k 10
```

`--source-type` 可以重复传入，允许值为 `policy`、`business_rule`、`workflow`、`requirement`、`api_spec`、`glossary` 和 `approved_case`。也可以用 `--module` 限定模块。

### 3.4 生成测试用例

直接传文本：

```bash
uv run testcase-ai generate \
  --project ecommerce-demo \
  --title "库存不足下单" \
  --text "库存不足时不能提交订单，需要覆盖库存边界" \
  --module 库存 \
  --output json \
  --output xlsx
```

或者传入 `.md`、`.markdown`、`.txt`、`.docx` 需求文件：

```bash
uv run testcase-ai generate \
  --project ecommerce-demo \
  --requirement ./需求文档.docx
```

返回结果中的 `status` 通常为：

- `succeeded`：生成成功；
- `needs_clarification`：证据不足或冲突，需要先回答澄清问题；
- `failed`：模型响应或其他运行步骤失败。

JSON 和 Excel 草稿会写入 `outputs/<project_id>/<generation_id>/`，该目录默认不提交到 Git。

## 4. 启动 HTTP API

```bash
uv run testcase-ai serve
```

默认地址：

- 健康检查：`http://127.0.0.1:8000/health`；
- Swagger UI：`http://127.0.0.1:8000/docs`；
- OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`。

可先测试：

```bash
curl http://127.0.0.1:8000/health
```

检索示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/retrieval/search \
  -H 'Content-Type: application/json' \
  -d '{
    "project_id": "ecommerce-demo",
    "query": "订单超时后库存如何处理",
    "source_types": ["business_rule", "workflow"],
    "top_k": 8
  }'
```

生成示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/testcase-generations \
  -H 'Content-Type: application/json' \
  -d '{
    "project_id": "ecommerce-demo",
    "requirement": {
      "title": "超时订单释放库存",
      "content": "覆盖订单支付超时后释放库存的正常与异常场景"
    },
    "module_hint": "库存",
    "output_formats": ["json", "xlsx"]
  }'
```

API 还支持上传需求、继续澄清、下载导出和审批。准确请求结构以 Swagger UI 或 `contracts/*.json` 为准。

## 5. 接入自己的私有业务知识

不要修改公开演示包来存放真实资料。复制其目录结构，创建自己的项目：

```text
projects/my-project/
├── project.yaml
└── knowledge/
    ├── business-rules/
    ├── workflows/
    ├── requirements/
    ├── api-specs/
    ├── glossary/
    └── approved-cases/
```

项目目录名必须和 `project.yaml` 的 `id` 一致。最小清单示例：

```yaml
schema_version: "1"
id: my-project
name: 我的私有业务包
version: "1.0"
language: zh-CN

sources:
  - type: business_rule
    path: knowledge/business-rules
    include: ["**/*.md"]
  - type: workflow
    path: knowledge/workflows
    include: ["**/*.md"]
  - type: approved_case
    path: knowledge/approved-cases
    include: ["**/*.xlsx"]

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
```

知识源支持 Markdown、DOCX 和 XLSX；具体文件是否进入索引由每项 `include` 和 `exclude` 控制。修改知识后执行：

```bash
uv run testcase-ai project validate --project my-project
uv run testcase-ai knowledge sync --project my-project
```

当前 `.gitignore` 默认忽略除 `ecommerce-demo` 外所有 `projects/*/knowledge/` 正文，也完整忽略 `projects/home-service/`、`evals/`、`legacy/` 和 `policies/`。`project.yaml` 默认仍可能被 Git 看见；若清单本身包含敏感名称或路径，请把整个私有项目目录追加到本机 `.git/info/exclude`，例如：

```gitignore
/projects/my-project/
```

`.git/info/exclude` 只影响当前克隆，不会改变团队仓库的 `.gitignore`。

## 6. 审核后写入正式用例

AI 生成结果不会自动覆盖正式用例。审批要求：

1. 生成任务已经成功，且选中的用例不处于 `needs_clarification`；
2. 私有业务包存在 `knowledge/approved-cases/modules/<模块名>.xlsx`；
3. 工作簿包含 `project.yaml` 的 `excel.sheet` 工作表和约定表头；
4. 命令显式传入 `--confirm`。

```bash
uv run testcase-ai approve \
  --generation-id <generation_id> \
  --case-id <case_id> \
  --confirm
```

审批会先备份工作簿，再原子写入选中用例，最后自动发布新的知识版本。公开 `ecommerce-demo` 只提供 Markdown 演示用例，不提供可写回的正式 Excel，因此不要用它验证审批写回。

## 7. 检索评测

准备本地 Gold Set 后运行：

```bash
uv run testcase-ai eval retrieval \
  --dataset ./path/to/local-retrieval.yaml \
  --threshold 0.80
```

报告写入 `outputs/evaluations/`。真实 Gold Set 默认不提交到公开仓库，字段和指标说明见 [检索评测](evaluation.md)。

## 8. 开发与验证

```bash
uv run ruff check src tests scripts migrations
uv run pytest
```

查看所有命令：

```bash
uv run testcase-ai --help
uv run testcase-ai generate --help
```

修改 Pydantic 契约后可重新导出 JSON Schema：

```bash
uv run python scripts/export_contracts.py
```

## 9. 常见问题

### Cursor 显示的 Python 不对

先在仓库根目录执行 `uv sync --extra dev`，然后确认解释器为 `.venv/bin/python`；Windows 为 `.venv\\Scripts\\python.exe`。不要只打开 `src`，否则 `${workspaceFolder}` 会指向错误目录。

### `embedding model is not configured` 或 `generation model is not configured`

确认已经执行 `cp .env.example .env`，且 `.env` 的 `TESTCASE_AI_EMBEDDING_MODEL` 与 `TESTCASE_AI_LLM_MODEL` 非空。

### 无法连接 MySQL 或 Milvus

运行 `docker compose ps`。若服务还在 starting，等待 healthcheck 完成；再检查本机的 `3306`、`19530`、`9091` 端口是否被占用。

### Ollama 返回找不到模型

运行 `ollama list` 检查精确模型名，并让 `.env` 与列表保持一致。首次使用先执行 `ollama pull qwen3:1.7b` 和 `ollama pull bge-m3`。

### 修改知识后检索不到新内容

每次修改业务包后都要重新运行 `knowledge sync`。检索只使用当前项目已经发布的 active revision。

### 新电脑能否拿到本地真实业务资料

不能，也不应该自动拿到。GitHub 只提供代码、配置、文档和虚构演示包。真实业务知识需要通过公司的安全渠道单独复制到新电脑，再执行项目校验和知识同步。

## 10. 停止服务

停止 API 可在运行它的终端按 `Ctrl+C`。停止 Docker 服务：

```bash
docker compose down
```

该命令保留 volumes。只有确认不再需要本地 MySQL/Milvus 数据时，才考虑手工删除对应 volumes。
