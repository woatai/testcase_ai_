# 平台架构说明

## 业务包

每个业务包以 `project.yaml` 为唯一入口，显式声明知识来源、文件类型、包含/排除规则、检索参数和 Excel 格式。平台路径校验禁止清单逃逸到项目目录之外。

```text
projects/<project_id>/
├── project.yaml
└── knowledge/
    ├── policies/
    ├── business-rules/
    ├── workflows/
    ├── requirements/
    ├── api-specs/
    ├── glossary/
    └── approved-cases/
```

业务文件是唯一事实来源；MySQL 和 Milvus 只是可重建的运行索引。`outputs/`、备份文件、模块索引和临时 Excel 不会进入知识库。

## 知识版本

同步时根据项目清单、来源路径、来源类型和文件内容计算 fingerprint：

1. fingerprint 未变化时直接返回当前 revision；
2. 变化时创建 `building` revision；
3. 解析 Markdown/DOCX/XLSX，建立 logical ID 和 content hash；
4. 完成 Embedding 与 Milvus 写入后，将 revision 发布为 `published`；
5. 任一步失败时将 revision 标为 `failed`，继续使用上一个有效版本。

Markdown 按标题和长度分块；正式 Excel 按“一条用例一个块”处理，并保留用例编号、模块、版本、路径、测试点、步骤和预期结果。

## 检索

检索始终带 `project_id + active_revision_id` 过滤，并支持模块和来源类型过滤：

```text
查询 → Dense TopK ─┐
                    ├→ RRF → TopK 证据 + 来源 + 排名日志
查询 → BM25 TopK ──┘
```

中文 BM25 同时使用连续中文文本和二元字符，兼顾精确业务术语、编号及自然语言问法。Query Rewrite 和 Rerank 预留为配置项，一期默认关闭。

## 生成与安全

模型只能使用已提供的 `evidence_id`。模型输出先经过 Pydantic Schema，再检查引用是否真实存在、正式用例预期是否支持生成结论、是否与已批准用例精确重复。

- JSON 不合格时只允许自动修复一次；
- 关键规则缺失或证据结论冲突时返回 `needs_clarification`；
- 检索不可用时禁止静默退化为无 RAG 生成；
- 正式入库要求显式确认、选中用例 ID、备份和原子替换；
- 入库完成后自动创建新知识 revision。
