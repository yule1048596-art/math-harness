# Math Harness

一个面向数学专家成长的可运行原型。v0.2 已完成第二条纵向闭环：

1. 创建彼此隔离的工作区；
2. 录入题目、解答和可选的结构化数学表达式；
3. 使用受限解析器和 SymPy 验证答案；
4. 使用规则或结构化 LLM 从解答中提取候选方法卡；
5. 只有通过验证的方法才会自动晋级；
6. 在当前工作区内检索相关方法并生成解题计划；
7. 保存提取提供方、模型、响应 ID、证据、置信度与回退原因；
8. 用固定训练集和留出集度量学习前后的检索变化；
9. 用测试证明跨工作区不会发生数据泄漏。

## 快速开始

```bash
uv sync --no-editable --extra dev
uv run pytest
uv run --no-editable math-harness-demo --data-root demo-data
uv run --no-editable math-harness-eval --data-root eval-data
uv run --no-editable uvicorn math_harness.api:app --reload
```

API 启动后可访问 `http://127.0.0.1:8000/docs`。

## 可选的 OpenAI 结构化提取器

默认使用完全离线的规则提取器。启用 OpenAI 时：

```bash
uv sync --no-editable --extra dev --extra llm
export OPENAI_API_KEY="..."
export MATH_HARNESS_METHOD_EXTRACTOR="openai"
export MATH_HARNESS_OPENAI_MODEL="gpt-5.6-terra"
export MATH_HARNESS_OPENAI_REASONING_EFFORT="low"
uv run --no-editable uvicorn math_harness.api:app --reload
```

默认选择 GPT-5.6 Terra，以兼顾数学语义提取质量与成本；模型和 reasoning effort
都可以通过环境变量替换。实现采用 OpenAI Responses API 的 Pydantic 结构化输出，
并设置 `store=False`。如果 SDK、密钥、网络或模型请求失败，本次学习会自动回退到
规则提取器，错误原因仍会进入审计记录，原始题目不会丢失。

相关官方资料：

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-5.6 model guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6)

密钥只从环境读取，不要写入仓库。

## 成长评测

`data/pilot/` 包含 6 个渐进估计训练题和 6 个独立留出查询。评测命令会创建一个
新工作区，先测空知识库，再摄取训练集并复测，输出：

- `Hit@1`
- `Recall@K`
- `Mean Reciprocal Rank`
- `Zero-result rate`

当前 pilot 的 `top-3` 结果是学习前 Recall@K `0.0`、学习后 `1.0`。该数据集很小，
且使用了清晰的人工标签，只用于验证“摄取—验证—学习—检索—度量”工程闭环，
不能当作真实数学能力基准。

## 主要 API

```text
POST   /workspaces
POST   /workspaces/{id}/examples
GET    /workspaces/{id}/examples
GET    /workspaces/{id}/methods
PATCH  /workspaces/{id}/methods/{method_id}
POST   /workspaces/{id}/methods/search
POST   /workspaces/{id}/solve-plan
GET    /workspaces/{id}/learning-events
POST   /workspaces/{id}/evaluations
GET    /workspaces/{id}/evaluations
```

方法可由人工在 `pending_review`、`promoted`、`deprecated` 之间调整；每次状态变化
和每次评测都会写入该工作区自己的学习事件流。

## 当前边界

- pilot 仍主要针对文本形式的渐进估计案例；
- 自然语言题目始终保存，但只有提供 `math_payload` 时才进行确定性数学验证；
- 结构化表达式使用 Python/SymPy 风格，如 `sqrt(x**2 + x) - x`；
- 表达式经过 AST 白名单解析，不执行任意 Python；
- 每个工作区使用独立 SQLite 文件，并在每条记录上再次校验 `workspace_id`；
- LLM 只提取可复用方法，不负责给题目判真；验证器是独立的信任边界；
- 目前检索器是词项与标签基线，尚未接入向量检索或数学结构检索；
- 当前还不自动生成完整候选解，`solve-plan` 只返回可追溯的方法建议。

## 示例请求

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
