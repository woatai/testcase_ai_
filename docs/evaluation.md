# 检索评测

## 评测口径

评测数据由使用者在本地提供，使用自然语言需求作为查询，并将期望结果关联到本地正式用例编号。真实 Gold Set 不进入公共仓库。

指标：

- `HitRate@10`：Top10 中至少出现一条相关正式用例的查询比例；
- `Recall@10`：每条查询相关用例召回率的宏平均；
- `MRR@10`：第一条相关用例排名倒数的平均；
- 通过门槛：`Recall@10 ≥ 0.80`。

## 运行方式

```bash
uv run testcase-ai eval retrieval \
  --dataset ./path/to/local-retrieval.yaml \
  --threshold 0.80
```

结果文件由命令写入 `outputs/evaluations/`，该目录不进入 Git。

Gold Set 只代表对应本地知识包的回归样本。新增模块、Embedding 模型、分块策略、Rerank 或权重调整时，应扩充本地 Gold Set 后再比较指标。
