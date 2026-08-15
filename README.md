# testcase_ai_

`testcase_ai_` 是一个可切换本地知识包、支持本地模型或外部 OpenAI-compatible API、通过 RAG 增强的测试用例生成平台。

本公开仓库包含平台代码和一套完全虚构的电商演示知识包，可用于验证安装、知识同步、检索和生成主链路。真实业务知识、正式用例、评测集和历史资料应只放在本地，并由 `.gitignore` 排除。

平台提供两项核心能力：

1. 根据需求检索规则、Workflow、历史需求和正式测试用例；
2. 把检索证据交给大模型，生成带引用的 JSON/Excel 测试用例草稿。

AI 草稿不会自动覆盖正式用例。只有显式执行 `approve --confirm`，选中的用例才会进入正式知识包并重新建立索引。

## 架构

```text
CLI / HTTP API
      ↓
需求解析（文本、Markdown、DOCX）
      ↓
项目知识包 + MySQL 版本元数据 + Milvus 向量索引
      ↓
Dense + BM25 + RRF 混合检索
      ↓
本地 Ollama / 外部 OpenAI-compatible API
      ↓
Schema、证据一致性、重复检查
      ↓
JSON / Excel 草稿 → 人工审核 → 正式入库
```

第一次使用请先看 [完整使用指南](docs/usage.md)。详细设计见 [架构说明](docs/architecture.md)，当前方案对照见 [实施状态](docs/implementation-status.md)。

## 目录

```text
src/testcase_ai/       平台代码
projects/              可切换的业务知识包；ecommerce-demo 是公开虚构示例
prompts/               通用生成 Prompt
contracts/             对外 JSON Schema
tests/                 自动化测试
outputs/               草稿和评测结果，不进入 Git
```

默认示例包是 `projects/ecommerce-demo`。真实业务包可以按同一 `project.yaml` 结构在本地添加；除该演示包外，`projects/*/knowledge/` 的正文默认不会被 Git 跟踪。

## 本地启动

要求：Git、Python 3.11+、[uv](https://docs.astral.sh/uv/)、Docker，以及一个 OpenAI-compatible 模型服务。项目已验证 Ollama 的 `qwen3:1.7b` 与 `bge-m3`。

```bash
git clone https://github.com/woatai/testcase_ai_.git
cd testcase_ai_
uv sync --extra dev
cp .env.example .env
ollama pull qwen3:1.7b
ollama pull bge-m3
```

`.env.example` 已带本地 Ollama 的演示模型名；若使用其他 OpenAI-compatible 服务，再修改 `.env` 中的地址、密钥和模型名。

启动数据服务并建立知识索引：

```bash
docker compose up -d mysql etcd minio milvus
uv run alembic upgrade head
uv run testcase-ai project validate --project ecommerce-demo
uv run testcase-ai knowledge sync --project ecommerce-demo
uv run testcase-ai serve
```

打开 `http://127.0.0.1:8000/docs` 即可使用 API 文档。第二次同步相同内容会返回 `unchanged: true`，不会重复生成向量。完整的配置、CLI、HTTP API、业务包接入和常见问题见 [完整使用指南](docs/usage.md)。

## 检索与生成

```bash
uv run testcase-ai search \
  --project ecommerce-demo \
  --query "库存不足时能否提交订单" \
  --source-type business_rule \
  --top-k 10
```

```bash
uv run testcase-ai generate \
  --project ecommerce-demo \
  --title "库存不足下单" \
  --text "库存不足时不能提交订单，需要覆盖库存边界" \
  --module 库存 \
  --output json \
  --output xlsx
```

也可以传入需求文件：

```bash
uv run testcase-ai generate \
  --project ecommerce-demo \
  --requirement ./需求文档.docx
```

输出位于 `outputs/{project_id}/{generation_id}/`。如果生成结论与引用的正式用例不一致，任务会变为 `needs_clarification`，对应草稿不能审批入库。

## HTTP API

```bash
uv run testcase-ai serve
```

主要接口：

- `POST /api/v1/projects/{project_id}/validate`
- `POST /api/v1/projects/{project_id}/knowledge/sync`
- `POST /api/v1/requirements` 和 `/requirements/upload`
- `POST /api/v1/retrieval/search`
- `POST /api/v1/testcase-generations`
- `POST /api/v1/testcase-generations/{id}/continue`
- `GET /api/v1/testcase-generations/{id}/exports/{format}`
- `POST /api/v1/testcase-generations/{id}/approve`

OpenAPI 文档地址：`http://127.0.0.1:8000/docs`。

## 检索评测

评测命令可以读取本地 Gold Set；公开仓库不附带真实业务评测数据。评测方法见 [检索评测说明](docs/evaluation.md)。

## 开发检查

```bash
uv run ruff check src tests
uv run pytest
```

真实业务知识、Gold Set、策略文件和历史资料不会进入公共仓库；请在本地知识包目录中维护并运行同步。
