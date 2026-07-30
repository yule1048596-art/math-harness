# Math Harness

一个面向数学专家成长的可运行原型。v0.3 已完成第一条可验证解题闭环：

1. 创建彼此隔离的工作区；
2. 录入题目、解答和可选的结构化数学表达式；
3. 使用受限解析器和 SymPy 验证答案；
4. 使用规则或结构化 LLM 从解答中提取候选方法卡；
5. 只有通过验证的方法才会自动晋级；
6. 新题到来时只检索当前工作区已晋级的方法；
7. 由离线 SymPy 或可选的结构化 LLM 生成候选解；
8. 候选解必须经过独立验证器，不能由生成模型自行判定正确；
9. 保存每次尝试的输入、检索方法、候选解、生成轨迹和验证报告；
10. 根据已验证的结果更新实际使用方法的成功/失败计数；
11. 以新记录保存人工纠正，不覆盖原始错误历史；
12. 分别评测方法检索和端到端解题门禁，并保持工作区物理隔离。

## 快速开始

```bash
uv sync --no-editable --extra dev
uv run pytest
uv run --no-editable math-harness-demo --data-root demo-data
uv run --no-editable math-harness-eval --data-root eval-data
uv run --no-editable uvicorn math_harness.api:app --reload
```

API 启动后可访问 `http://127.0.0.1:8000/docs`。

## 候选解生成器

默认生成器是完全离线的 SymPy 实现，不需要密钥。它接受 `math_target`，支持：

- `exact_equivalence`
- `asymptotic_equivalence`
- `asymptotic_expansion`

生成器只提交候选答案；现有安全解析器和符号验证器拥有最终验收权。没有
`math_target` 时，离线生成会明确记为 `generation_failed`，不会编造答案。

## 可选的 OpenAI 结构化组件

方法提取默认使用离线规则，求解默认使用 SymPy。可以独立启用 OpenAI：

```bash
uv sync --no-editable --extra dev --extra llm
export OPENAI_API_KEY="..."

# 可选：方法提取
export MATH_HARNESS_METHOD_EXTRACTOR="openai"
export MATH_HARNESS_OPENAI_MODEL="gpt-5.6-terra"
export MATH_HARNESS_OPENAI_REASONING_EFFORT="low"

# 可选：候选解生成
export MATH_HARNESS_SOLVER="openai"
export MATH_HARNESS_OPENAI_SOLVER_MODEL="gpt-5.6-sol"
export MATH_HARNESS_OPENAI_SOLVER_REASONING_EFFORT="medium"
export MATH_HARNESS_OPENAI_SOLVER_TIMEOUT_SECONDS="45"

uv run --no-editable uvicorn math_harness.api:app --reload
```

方法提取默认选择 GPT-5.6 Terra/low，候选求解默认选择
GPT-5.6 Sol/medium。两条路径都使用 OpenAI Responses API 的 Pydantic
结构化输出并设置 `store=False`。SDK、密钥、网络或请求失败时，提取会回退到规则，
求解会回退到 SymPy；提供方、模型、响应 ID、耗时和错误原因仍会进入审计记录。
模型输出只包含简洁的用户可见推导，不要求或保存隐藏思维链。

相关官方资料：

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-5.6 model guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6)

密钥只从环境读取，不要写入仓库。

## 成长评测

`data/pilot/` 包含 6 个渐进估计训练题和 6 个独立留出查询。评测命令会创建一个
新工作区，先测空知识库，再摄取训练集并复测，同时运行离线解题门禁。输出：

- `Hit@1`
- `Recall@K`
- `Mean Reciprocal Rank`
- `Zero-result rate`
- `Verified / needs-review / rejected / generation-failure rate`

当前 pilot 的 `top-3` 结果是学习前 Recall@K `0.0`、学习后 `1.0`。该数据集很小，
且使用了清晰的人工标签。离线求解器可自动验证其中 5/6 个结构化案例；
Gamma/Stirling 案例会诚实地报告生成失败。它们只用于验证工程闭环，不能当作
真实数学能力基准。

## 主要 API

```text
POST   /workspaces
POST   /workspaces/{id}/examples
GET    /workspaces/{id}/examples
GET    /workspaces/{id}/methods
PATCH  /workspaces/{id}/methods/{method_id}
POST   /workspaces/{id}/methods/search
POST   /workspaces/{id}/solve-plan
POST   /workspaces/{id}/solve
GET    /workspaces/{id}/attempts
GET    /workspaces/{id}/attempts/{attempt_id}
POST   /workspaces/{id}/attempts/{attempt_id}/corrections
GET    /workspaces/{id}/learning-events
POST   /workspaces/{id}/evaluations
GET    /workspaces/{id}/evaluations
POST   /workspaces/{id}/solve-evaluations
GET    /workspaces/{id}/solve-evaluations
```

方法可由人工在 `pending_review`、`promoted`、`deprecated` 之间调整；每次状态变化
和每次评测都会写入该工作区自己的学习事件流。求解评测不会保存普通尝试，也不会
修改方法成功/失败统计。

## 当前边界

- pilot 仍主要针对文本形式的渐进估计案例；
- 摄取时只有提供 `math_payload` 才进行确定性数学验证；求解时对应字段是
  `math_target`，它不包含答案；
- 结构化表达式使用 Python/SymPy 风格，如 `sqrt(x**2 + x) - x`；
- 表达式经过 AST 白名单解析，不执行任意 Python；
- 每个工作区使用独立 SQLite 文件，并在每条记录上再次校验 `workspace_id`；
- LLM 可以提取方法或生成候选解，但不负责给答案判真；验证器是独立信任边界；
- 目前检索器是词项与标签基线，尚未接入向量检索或数学结构检索；
- 成功/失败计数是可审计的在线反馈，不是底层模型权重微调；
- 离线求解器只覆盖有限的表达式等价与级数任务，不是通用定理证明器；
- 当前符号计算在进程内运行；面向不受信任的多用户服务前，还应加入进程级
  CPU/内存/时间限制。

## 示例：摄取一道已解题

```json
{
  "problem": "求 x→∞ 时 sqrt(x^2+x)-x 的渐进展开到 O(x^-2)",
  "solution": "先乘共轭式有理化，再令 t=1/x，并使用泰勒展开，得到 1/2-1/(8x)+O(x^-2)。",
  "tags": ["渐进估计", "根式", "无穷远"],
  "math_payload": {
    "expression": "sqrt(x**2 + x) - x",
    "expected": "1/2 - 1/(8*x)",
    "variable": "x",
    "point": "oo",
    "remainder_power": 2
  }
}
```

## 示例：生成并验证新题

```json
{
  "problem": "求 sqrt(x^2+x)-x 在 x→∞ 时到 O(x^-2) 的渐进展开",
  "tags": ["asymptotic", "radical"],
  "top_k": 3,
  "math_target": {
    "expression": "sqrt(x**2 + x) - x",
    "variable": "x",
    "point": "oo",
    "mode": "asymptotic_expansion",
    "remainder_power": 2
  }
}
```

提交到 `POST /workspaces/{id}/solve`。返回的 `SolutionAttempt.status` 可能是
`verified`、`needs_review`、`rejected` 或 `generation_failed`。只有 `verified`
和 `rejected` 会分别给实际使用且来自本次检索的方法记一次成功或失败。

## 数据目录

```text
.math_harness/
  registry.sqlite3
  workspaces/
    <workspace-id>/
      workspace.sqlite3
```

这个布局优先保证本地原型的物理隔离。转为多用户服务时，应迁移到 PostgreSQL、行级权限和按工作区过滤的向量索引。

## 版本归档

项目从 `v0.2.0` 起使用带注释的 Git 标签保存版本。推送 `vX.Y.Z` 标签后，
GitHub Actions 会在干净环境中重新测试和构建，并创建带 wheel、source
distribution 和标准源码归档的 GitHub Release。完整步骤见
[`RELEASING.md`](RELEASING.md)。

## License

本项目采用 [MIT License](LICENSE)，允许在保留版权和许可声明的前提下使用、
复制、修改、合并、发布、分发、再许可和销售本软件。
