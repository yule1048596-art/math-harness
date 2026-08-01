# Math Harness

一个面向数学专家成长的本地优先 Harness。v0.7.0 接通了第一条安全的对话成长闭环：
每次成功求解会自动形成带来源的知识草稿，macOS App 提供例题与方法草稿复核队列；
只有独立数学验证通过且经人工确认的内容才能晋级正式方法库。

v0.5.1 收紧了成长系统最关键的三条边界：未经复核的数据不能改写已晋级知识，重叠方法
合并不能重复应用，训练与全部留出集必须在评测开始前通过全局隔离检查。

v0.5.0 解决了「数据量是所有结论的天花板」这个问题：加入可验证的语料生成器、把
检索升级为算子树上的叶到根路径，并提供人工确认的方法卡去重合并。

v0.4.0 曾解决两处挡住「不断变强」的结构性问题：方法卡内容不再被冻结，检索也不再
只看词面。

v0.3.3 之前，方法卡一旦创建内容就永久冻结，后续例子只能给它加计数——系统只会变得
更自信，不会变得更懂。同时检索是词袋相似度，而数学题的判别信息在结构里（根式之差、
趋近点、余项阶），不在词面。

在此之上，v0.3.3 建立的信任边界保持不变：

1. 创建彼此隔离的工作区；
2. 录入题目、解答，或直接在对话中求解；
3. 使用受限解析器和 SymPy 验证答案；
4. 使用规则或结构化 LLM 从解答中提取候选方法卡；
5. 对话结果自动进入待审区并关联原始 `SolutionAttempt`；只有数学验证通过且由人工
   复核例题与方法草稿后才会晋级；
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

## v0.7.0 新增：对话成长闭环

- 每次持久化求解和人工纠正都会自动生成一条 `conversation` 来源的知识草稿，保存
  原题、最终答案、推导步骤、验证报告、候选方法全文和 `source_attempt_id`。同一次
  求解重复捕获保持幂等；生成失败会留下审计事件而不会制造空答案。
- macOS 知识侧栏新增“待复核 / 方法卡”双视图。复核卡完整展示答案、独立验证状态、
  即将写入的方法名称、目标与步骤，并支持复核意见、批准和驳回。
- App 使用 MiMo 求解时默认也让 MiMo 提炼模板外的新方法；设置中可关闭这次额外请求，
  改用免费、确定性的内置规则模板。
- 晋级只能从例题复核发生。缺少 `math_target` 或验证未通过的草稿不能批准；原来直接
  把待审方法卡改成 `promoted` 的入口已关闭，避免绕过来源证据。
- 驳回草稿会保留审计历史，并自动拒绝已经没有其他例题证据的孤立待审方法；不会影响
  已晋级知识或其他草稿共享的方法。
- SQLite 会自动增加来源、方法草稿、复核时间与意见字段；旧工作区首次打开自动迁移。

新增 API：

```text
POST /workspaces/{id}/attempts/{attempt_id}/capture
POST /workspaces/{id}/examples/{example_id}/review
```

## v0.6.0 新增：原生 macOS Alpha

- 使用 SwiftUI 构建三栏原生界面：工作区侧边栏、数学对话、方法知识库；支持创建和
  切换工作区、查看历史尝试、提交结构化验算目标，以及查看或废弃方法卡。
- App 自动启动独立 Python helper。helper 只监听 `127.0.0.1` 的系统随机端口，每次
  启动生成新的 256 位令牌，全部 API 路由都必须通过 Bearer Token 鉴权。
- PyInstaller 把 Python 3.12、SymPy、FastAPI 和可选模型客户端一并放进 App，最终用户
  无需预装 Python、`uv` 或浏览器。
- MiMo API Key 保存到 macOS Keychain；非秘密偏好保存在系统设置中。切换求解器后 App
  会重启本地引擎，不把密钥写入项目、数据库或日志。
- 工作区数据默认位于 `~/Library/Application Support/Math Harness/`；App
  意外退出时，helper 会检测父进程消失并自行停止。
- 新增 Apple Silicon 独立包构建、签名、DMG/公证脚本、Swift 协议自检和打包后端到端
  验证。完整开发与分发说明见 [`macos/README.md`](macos/README.md)。

## v0.5.1 新增

### 晋级知识不可被待审输入反向污染

`reviewed=false` 的例子仍可创建和丰富 `pending_review` 方法卡，但不能再改写
`promoted` 方法的内容、签名、计数或来源关系。若同一 key 的待审卡后来首次获得可信
样本，可信草稿会重建卡片的规范内容和结构签名，不会继承早先模型生成的名称与步骤。
人工废弃或拒绝的方法也不会被后续摄取静默复活。被忽略的尝试仍写入学习事件，审计
链不会消失。

### 合并提案按事务处理

应用合并时会在 SQLite 写锁内重新读取提案与两张方法卡。一次合并成功后，所有触及
这两张卡的其他待处理边都会变为 `stale`；重新扫描时，仅仍然有效的 stale 边会恢复为
`pending`。主卡选择先保留 `promoted` 卡，再比较样本量和创建时间，避免大体量待审卡
吞掉可信卡。

### 评测隔离和验证边界加固

- 成长评测在创建工作区前检查所有训练文件：相同数学任务不得重复，任何训练任务不得
  与检索或求解留出集重叠；评测显式锁定规则提取器和离线 SymPy 求解器，本地 `.env`
  不会把它切换成联网模型。
- 生成语料同时排除手工种子和全部留出表达式，现为 22 条手工题 + 113 条生成题，共
  135 个隔离的训练任务。
- 精确等价验证会比较主变量及所有参数的定义域；`a/a = 1` 只有声明 `a != 0` 等足够
  假设时才通过。
- 只有渐进展开模式会移除候选末尾的 `O(...)`；精确等价答案中的 Big-O 不再被误当成
  可丢弃文本。
- 方法历史分同时考虑成功和失败反馈，同等成功量下，失败较多的方法会自然降权。

## v0.5.0 新增

### 可验证的语料生成器

`math-harness-dataset` 按方法家族程序化生成训练语料。两条硬约束：

- **不写新的数学代码**：答案由现成的离线求解器算出（覆盖不到的形状可在家族里显式
  写出候选答案），正确性一律由 `SolutionVerifier` 裁决。生成数据不因为「是我们自己
  生成的」就被信任，它走和人工数据完全相同的那条 SymPy 裁决线。
- **完全确定性**：参数网格是显式整数序列，无随机，输出排序。同一条命令产出逐字节
  相同的文件，否则评测不可复现。

生成器会**主动排除手工种子和留出集里出现过的表达式**。只「不生成留出集」是不够
的——参数网格会顺手覆盖掉留出题，那样评测测的是记忆而不是检索。这条由代码强制并
有测试保证。

```bash
uv run --no-editable math-harness-dataset --out data/pilot/asymptotic_train_generated.jsonl
```

### 叶到根路径检索

算子集合是扁平的，`sqrt` 和 `division` 只是两个标签，丢掉了「谁套在谁外面」。
v0.5.0 按 approach0 在数学公式检索上的做法，改为在算子树上提取**叶到根路径**
（保留最靠近叶子的 3 层），并按 IDF 加权——`Add/VAR` 几乎每个方法都有，判别力接近
零；`Gamma/Add/VAR` 只属于少数方法，命中时权重更大。

路径在结构分里占 0.50，其余低容量特征保留下来，用于方法卡样本很少、路径统计还不
可靠时兜底。

### 方法卡去重合并（只提议，人工确认）

`upsert_method` 按 `(workspace_id, method_key)` 精确匹配，LLM 给同一方法起不同 slug
就会各建一张卡。新增扫描接口按「结构签名相似度 × 0.5 + 文本相似度 × 0.5」找出疑似
重复对，写入待审提案。

**默认只提议，不自动合并。** 误合并会永久丢失知识，而合并的收益只是整洁——这个不
对称决定了默认必须人工确认。阈值默认 `0.75`，可经 `MATH_HARNESS_DEDUP_THRESHOLD`
调整。

确认后：内容按并集累积、计数相加、签名逐项合并、来源例子归并到主卡，副卡置为
`deprecated` 而非删除。不删除既是审计要求，也是技术必需——`attempt_methods` 的外键
是 `ON DELETE RESTRICT`，被尝试记录引用过的方法卡本来就删不掉。

## v0.4.0 新增

### 方法卡可演进

新例子会把新的适用条件、失败模式和标签**并集累积**进已有方法卡，而不再只是加计数。
`name`、`goal`、`procedure` 保留首版——procedure 是一段有序算法，把不同例子的步骤
混在一起只会得到不连贯的流程。各字段有硬上限（适用条件 20、失败模式 20、标签 24），
防止连续摄取几百道题后卡片膨胀成垃圾场。

每次内容改写前的旧版本都会存档，可经
`GET /workspaces/{id}/methods/{method_id}/versions` 回溯，原始理解不被覆盖。内容
真正变化时才写快照并记 `method_content_evolved` 学习事件。

注意：离线规则提取器的 7 个模板是静态的，同一方法每次给出相同草稿，内容不会演进。
演进发生在 LLM 提取路径上。

### 结构化检索

请求带 `math_target` 时，检索改用数学结构而非词面。结构特征由 `SafeMathParser`
解析后确定性提取——趋近点类型、算子集合（根式、指数、对数、三角、Gamma、阶乘、
含变量的幂）以及结构标志（根式相减、抵消风险、含参数、有理式、无穷远处振荡等）。
方法卡从它关联的**已验证**例子累积「结构适用签名」，全程不调模型，零额外成本。

打分分两条路径：没有 `math_target` 时完全保持 v0.3.x 的词面公式；有结构信息时按
`结构 0.45 + 词面 0.20 + 标签 0.25 + 历史 0.10` 重新配权。方法卡签名为空时结构分为
0，自然退回词面路径，冷启动安全。表达式解析失败降级为空特征，绝不让检索整体失败。

## 快速开始

### macOS App

开发模式（只安装 Apple Command Line Tools 即可）：

```bash
./scripts/run_macos_app.sh
```

构建包含 Python 运行时的独立 Apple Silicon App：

```bash
./scripts/build_macos_app.sh
./scripts/test_macos_bundle.sh
MATH_HARNESS_SKIP_APP_BUILD=true ./scripts/package_macos_dmg.sh
```

### API / Python 开发

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

`data/pilot/` 包含 22 个经人工复核的训练题、113 个程序生成并经 SymPy 验证的训练题
（共 135 个训练任务，覆盖全部 4 种验证模式）、两套方法检索留出集和 6 个独立求解
留出题。评测命令会先做全局隔离预检，再创建新工作区；先测空知识库，摄取训练集并
复测，最后只在未见求解集上运行离线解题门禁。输出：

- `Hit@1`
- `Recall@K`
- `Mean Reciprocal Rank`
- `Zero-result rate`
- `Verified / needs-review / rejected / generation-failure rate`
- `Fallback / correction-attempt / correction-success rate`

### 两套检索口径

`asymptotic_holdout.jsonl`（旧口径，6 条）的查询其实是**方法卡的改写句**，例如
「根式相减出现无穷减无穷，怎样先消除抵消再估计？」，且 `tags` 几乎逐字复制了方法
模板的 tags。它衡量的是方法写入路径和读取路径接通了，不是检索能泛化。保留它只作
向后兼容的回归对照。

`asymptotic_retrieval_v2.jsonl`（新口径，18 条）的查询是**真实数学题**，带
`math_target`，`tags` 用用户会写的中文领域词，不复制模板的英文 tags。这是反映真实
检索能力的标尺。

`top-3` 结果：

| 口径 | 指标 | v0.3.3 | v0.4.0 | v0.5.0 | v0.5.1 |
|---|---|---|---|---|---|
| 旧（方法卡改写句） | Hit@1 | 1.0 | 1.0 | 1.0 | 1.0 |
| **新（真实题面）** | **Hit@1** | **0.389** | **0.889** | **1.0** | **1.0** |
| 新（真实题面） | Recall@K | 0.778 | 1.0 | 1.0 | 1.0 |
| 新（真实题面） | MRR | 0.519 | 0.944 | 1.0 | 1.0 |

v0.3.3 → v0.4.0 的差距说明了旧指标的水分：同一套检索，换成真实题面后 Hit@1 从
`1.0` 掉到 `0.389`。

### 关于 v0.5.0 那个 1.0，必须说清楚

18 题全对，但**其中 14 题在训练语料里存在路径完全相同的样本**。路径表示对系数不
敏感（数字统一归为 `NUM`），所以 `sqrt(x²+3x)-x` 和留出题 `sqrt(x²+5x)-x` 在结构上
是同一道题。这 14 题测的是「匹配见过的形状」——这本来就是检索该做的事，但不是泛化。

真正的泛化信号是另外 4 题：`radical-pair-difference`、`gamma-ratio`、
`factorial-growth-rate`、`root-power-limit` 是训练语料里没有的形状，**这 4 题也全部
命中**。样本只有 4 条，统计上说明不了太多，但方向是对的。

换句话说：`1.0` 不代表检索已经解决，只代表**当前这套留出集对当前这套检索已经太
容易了**。更难的留出集（更多新形状、跨方法家族的干扰项）是下一步该做的事。

全部训练语料与全部留出集**不存在相同任务重叠**，这一点由评测入口全局检查；生成器
还会更保守地按表达式排除手工种子和留出集，并有测试保证。

独立求解集仍是 4/6 自动验证通过，定义域空洞和振荡无等价两题安全进入复核——这两条
是设计预期的安全停止，不是待修的失败。

该数据集仍然很小，只用于验证工程闭环，不能当作真实数学能力基准。

## 主要 API

```text
POST   /workspaces
POST   /workspaces/{id}/examples
GET    /workspaces/{id}/examples
POST   /workspaces/{id}/examples/{example_id}/review
GET    /workspaces/{id}/methods
GET    /workspaces/{id}/methods/{method_id}/versions
POST   /workspaces/{id}/methods/merge-proposals
GET    /workspaces/{id}/methods/merge-proposals
POST   /workspaces/{id}/methods/merge-proposals/{pid}/apply
POST   /workspaces/{id}/methods/merge-proposals/{pid}/reject
PATCH  /workspaces/{id}/methods/{method_id}
POST   /workspaces/{id}/methods/search
POST   /workspaces/{id}/solve-plan
POST   /workspaces/{id}/solve
GET    /workspaces/{id}/attempts
GET    /workspaces/{id}/attempts/{attempt_id}
POST   /workspaces/{id}/attempts/{attempt_id}/capture
POST   /workspaces/{id}/attempts/{attempt_id}/corrections
GET    /workspaces/{id}/learning-events
POST   /workspaces/{id}/evaluations
GET    /workspaces/{id}/evaluations
POST   /workspaces/{id}/solve-evaluations
GET    /workspaces/{id}/solve-evaluations
```

方法晋级必须来自“验证通过的例题 + 人工复核”；方法卡仍可人工废弃。每次复核、状态
变化和评测都会写入该工作区自己的学习事件流。求解评测不会保存普通尝试，也不会修改
方法成功/失败统计。

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
- 检索在提供 `math_target` 时使用叶到根路径与结构签名，否则退回词项与标签基线；
  尚未接入向量检索；
- 留出集永远保持人工标注，绝不由生成器产出；生成器还会主动排除留出集中出现过的
  表达式——只「不生成留出集」不足以防止参数网格顺手覆盖留出题；
- 生成语料的方法归属是「由构造决定」而非从解答推断，这对训练是监督数据、可以接受，
  但指标一侧靠人工留出集隔离；
- 方法卡签名只累积**已验证且已复核**例子的结构；同一道题抽出的多个方法共享同一签名，
  区分能力来自例子的多样性；
- 方法卡去重需人工确认；扫描是显式触发的，不挂在摄取路径上；副卡置为 `deprecated`
  而非删除；重叠提案会在一次合并后失效，重新扫描可恢复仍有效的边；
- 成功/失败计数是可审计的在线反馈，不是底层模型权重微调；
- 离线求解器只覆盖有限的表达式等价与级数任务，不是通用定理证明器；
- 当前符号计算在进程内运行；面向不受信任的多用户服务前，还应加入进程级
  CPU/内存/时间限制。
- macOS Alpha 已把整个服务与 GUI 分成两个进程，但单次 SymPy 任务仍在 helper 主进程
  内运行；超时、取消和每道题独立 worker 尚未完成。
- macOS 界面当前以可选择的等宽文本展示表达式，尚未加入离线 LaTeX 排版。

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

macOS App 使用系统 Application Support 目录作为根目录：

```text
~/Library/Application Support/Math Harness/
  registry.sqlite3
  Logs/backend.log
  workspaces/
    <workspace-id>/workspace.sqlite3
```

## 版本归档

项目从 `v0.2.0` 起使用带注释的 Git 标签保存版本。推送 `vX.Y.Z` 标签后，
GitHub Actions 会在干净环境中重新测试和构建，并创建带 wheel、source
distribution、Apple Silicon macOS App ZIP/DMG 和标准源码归档的 GitHub Release。完整步骤见
[`RELEASING.md`](RELEASING.md)。

## License

本项目采用 [MIT License](LICENSE)，允许在保留版权和许可声明的前提下使用、
复制、修改、合并、发布、分发、再许可和销售本软件。
