# 已确认方案与实施状态对照

| 方案要求 | 当前实现 | 状态 |
| --- | --- | --- |
| 当前仓库内重构 | 平台代码已整理到 `main` | 已实现 |
| CLI + API 共用核心 | Typer 与 FastAPI 调用同一服务层 | 已实现 |
| MySQL + Milvus，同步优先 | Docker Compose、本地同步任务、无 Redis/RQ | 已验证 |
| 独立业务知识包 | 各项目使用同一 Manifest 契约，真实内容由使用者本地提供 | 已实现 |
| 保留独立 Workflow | 每个项目可声明独立 `workflows/` | 已实现 |
| 文本、Markdown、DOCX 输入 | CLI/API 文件解析器与测试 | 已实现 |
| Dense + BM25 + RRF | 项目级混合检索与过滤 | 已验证 |
| Query Rewrite/Rerank 默认关闭 | Manifest 中保留开关，未进入一期执行链 | 符合一期边界 |
| 本地模型和外部 API | 统一 OpenAI-compatible Generation/Embedding Provider | 已验证本地 Ollama |
| JSON/Excel 草稿 | Schema 输出和兼容原主表的 Excel 导出 | 已验证 |
| 人工审批后入库 | 显式确认、备份、原子写入、幂等记录、重新索引 | 临时业务包集成测试通过 |
| 正式资产保护 | 由本地知识包和审批备份机制负责 | 已实现 |
| 正式用例检索评测 | 支持使用者提供本地 Gold Set 和 Recall 门禁 | 已实现 |
| 数据库设计文档 | 未加入知识源 | 符合一期边界 |
| 网页、登录、多租户、异步队列 | 未实现 | 符合一期边界 |

## 与原方案的可见差异

1. API 为了同时清晰支持 JSON 和文件上传，将需求入口拆为 `/requirements` 与 `/requirements/upload`；生成契约不变。
2. 当前正式用例去重已实现精确归一化；语义相似度 0.90 的审批前去重尚未启用，避免在没有专门 Gold Set 前误删相似但前置条件不同的用例。
3. Rerank 仅保留开关，没有接入外部 Rerank 服务；一期以已经通过评测的 Dense+BM25+RRF 为基线。
4. 自动审批写盘具备实现和单元边界保护；真实审批仍需用户选中草稿并显式确认。
