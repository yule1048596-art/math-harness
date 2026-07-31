# Math Harness

一个面向数学专家成长的可运行原型。v0.3.3 把数学验证和成长反馈之间的信任边界
收紧为默认安全：

1. 创建彼此隔离的工作区；
2. 录入题目、解答和可选的结构化数学表达式；
3. 使用受限解析器和 SymPy 验证答案；
4. 使用规则或结构化 LLM 从解答中提取候选方法卡；
5. 新录入知识默认进入待审区，只有数学验证通过且明确标记 `reviewed=true`
   的精选案例才会自动晋级；
6. 新题到来时只检索当前工作区已晋级的方法；
7. 由离线 SymPy 或可选的结构化 LLM 生成候选解；
8. 先把模型常见数学记法规范化为安全解析器可接受的有限表达式；
9. 候选答案、最终步骤、定义域、方向和参数假设必须经过独立验证器；
10. “无等价式、无极限、条件成立”等非表达式结论安全进入复核，不会被强制
    改写成伪表达式；
11. 验证失败时允许主模型根据确定性反馈纠正一次，再失败才交给 SymPy；
12. 分阶段保存首答、纠错和兜底的候选解、响应 ID、原始输出与验证报告；
13. 只根据 `feedback_method_keys` 更新真正具备归因资格的方法；SymPy 兜底和
    未显式指定方法的人工纠正不参与计分；
14. 以新记录保存人工纠正，不覆盖原始错误历史；
15. 分别使用独立留出集评测方法检索和端到端解题门禁。

## 快速开始

```bash
uv sync --no-editable --extra dev
uv run pytest
uv run --no-editable math-harness-demo --data-root demo-data
uv run --no-editable math-harness-eval --data-root eval-data
uv run --no-editable uvicorn math_harness.api:app --reload
```

API 启动后可访问 `http://127.0.0.1:8000/docs`。

## 小米 MiMo 配置

v0.3.1 起原生支持小米 MiMo 的 OpenAI-compatible API。推荐从模板创建本地配置：

```bash
cp .env.example .env
uv sync --no-editable --extra dev --extra llm
```

然后在 `.env` 中填写：

```dotenv
MATH_HARNESS_METHOD_EXTRACTOR=rules
MATH_HARNESS_SOLVER=mimo
MIMO_API_KEY=your-mimo-key
MATH_HARNESS_MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MATH_HARNESS_MIMO_MODEL=mimo-v2.5-pro
MATH_HARNESS_MIMO_SOLVER_REASONING_EFFORT=none
MATH_HARNESS_MIMO_TIMEOUT_SECONDS=60
MATH_HARNESS_VERIFICATION_REPAIR=true
MATH_HARNESS_VERIFICATION_FALLBACK=true
```

服务启动时会自动加载 `.env`，但不会覆盖已在 Shell 中导出的变量。真实 `.env`
已被 Git 忽略；不要把密钥写进 `.env.example`、源码、测试或提交记录。

`sk-` 密钥使用上面的按量 Base URL；`tp-` Token Plan 密钥应使用订阅页面提供的
专用 Base URL。旧 MiMo V2 模型已经退役，新配置使用 `mimo-v2.5-pro`。

MiMo 当前支持 Responses API 的 JSON Object 模式，但不保证严格符合业务 Schema。
Harness 会在本地用 Pydantic 校验 JSON，失败时携带 Schema 错误重试一次。它还会
规范化最终数学表达式中的 Big-O、小写 `e`、`ln`、Unicode 运算符和 `^`；数学验证
仍失败时，默认进入一次模型纠错阶段，再失败才使用 SymPy。SDK 传输层重试显式设为
`0`，避免隐式放大请求次数；一次 JSON 纠错阶段仍可能因本地 Schema 失败调用模型
两次。
若不希望产生额外模型费用，可把 `MATH_HARNESS_VERIFICATION_REPAIR` 设为 `false`；
确定性回退也可独立关闭。

真实 `mimo-v2.5-pro / none` 的 v0.3.3 信任边界回归包含普通根式、定义域空洞、
正参数和振荡无等价四题：普通根式经可审计 SymPy 兜底通过，`a>0` 参数题由 MiMo
直接通过；定义域和振荡题安全停在 `needs_review`，不再被错误标绿，也不修改方法
反馈。困难题可以改为 `high`，同时在请求中设置 `max_output_tokens: 8000`，并将
超时提高到约 180 秒。

官方资料：

- [MiMo Responses API](https://mimo.mi.com/docs/en-US/api/chat/responses)
- [MiMo 模型选择](https://mimo.mi.com/docs/quick-start/summary/model)
- [MiMo 工具与 Base URL 配置](https://mimo.mi.com/docs/integration/tools-overview)

## 候选解生成器

默认生成器是完全离线的 SymPy 实现，不需要密钥。它接受 `math_target`，支持：

- `exact_equivalence`
- `limit`
- `asymptotic_equivalence`
- `asymptotic_expansion`

生成器只提交候选答案；现有安全解析器和符号验证器拥有最终验收权。没有
`math_target` 时，离线生成会明确记为 `generation_failed`，不会编造答案。

启用 OpenAI-compatible 生成器时，默认恢复顺序为：

1. 规范化 `answer_expression`；
2. 执行安全解析、定义域/极限验证，并检查最终步骤与答案表达式一致；
3. 未通过时把候选解与验证报告交回主模型纠正一次；
4. 纠正仍未通过时使用 SymPy 重新生成，并再次验证。

验证通过后，公开的 `answer_text` 由服务端规范为已验表达式，模型原始文本保留在
`generation.stages` 中。轨迹还记录 `normalization_actions`、
`correction_attempted`、`correction_succeeded`、`verification_fallback_used`
和恢复说明。人工纠正、非表达式结论以及缺少 `math_target` 的自然语言请求不会被
自动恢复流程改写。

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

密钥只从环境或本地 `.env` 读取，不要写入仓库。

## 成长评测

`data/pilot/` 包含 6 个经人工复核的训练题、6 个方法检索留出查询和 6 个独立求解
留出题。评测命令会创建一个新工作区，先测空知识库，再摄取训练集并复测，最后只在
未见求解集上运行离线解题门禁。输出：

- `Hit@1`
- `Recall@K`
- `Mean Reciprocal Rank`
- `Zero-result rate`
- `Verified / needs-review / rejected / generation-failure rate`
- `Fallback / correction-attempt / correction-success rate`

当前 pilot 的 `top-3` 结果是学习前 Recall@K `0.0`、学习后 `1.0`。独立求解集
中 4/6 自动验证通过，定义域空洞和振荡无等价两题安全进入复核。该数据集很小且
使用了清晰的人工标签，只用于验证工程闭环，不能当作真实数学能力基准。

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
- `reviewed=false` 是默认值；未经可信人工或精选数据流水线复核的解答和方法只能
  进入 `pending_review`；
- `math_target.assumptions` 支持 `real`、`positive`、`negative`、`nonzero`、
  `integer`、`nonnegative` 和 `nonpositive`；`direction` 支持 `two_sided`、
  `left` 和 `right`；
- 结构化表达式使用 Python/SymPy 风格，如 `sqrt(x**2 + x) - x`；
- 表达式经过 AST 白名单解析，不执行任意 Python；
- 每个工作区使用独立 SQLite 文件，并在每条记录上再次校验 `workspace_id`；
- LLM 可以提取方法或生成候选解，但不负责给答案判真；验证器是独立信任边界；
- 模型纠错只有一个阶段，但 JSON Schema 本地重试可能让该阶段产生两次模型调用；
  SDK 传输重试为 `0`，模型纠错可用环境变量关闭；
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
  "reviewed": true,
  "math_payload": {
    "expression": "sqrt(x**2 + x) - x",
    "expected": "1/2 - 1/(8*x)",
    "variable": "x",
    "assumptions": {},
    "point": "oo",
    "direction": "two_sided",
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
和 `rejected` 才可能产生反馈，并且只更新返回结果中的 `feedback_method_keys`；
SymPy 兜底默认返回空列表。

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
