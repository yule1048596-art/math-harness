# Math Harness for macOS

v0.11.0 在持久数学对话上增加了工作区级“记忆工坊”：用户画像、学习目标、
讲解偏好和专题背景可在同一工作区的新对话中继续使用。普通聊天和带 SymPy
独立验算的求解共用一条时间线；Python/SymPy 服务作为独立子进程运行。

## 开发运行

最低系统版本为 macOS 14。只安装 Apple Command Line Tools 也可以编译和运行：

```bash
./scripts/run_macos_app.sh
```

开发模式会从仓库通过 `uv run math-harness-server` 自动启动后台。也可以连接已经运行的
服务：

```bash
MATH_HARNESS_BACKEND_URL=http://127.0.0.1:8000 \
  ./scripts/run_macos_app.sh
```

核心协议自检：

```bash
swift run --package-path macos/MathHarnessApp MathHarnessCoreChecks
```

独立包端到端自检（需要 `jq`）：

```bash
./scripts/test_macos_bundle.sh
```

## 构建独立 App

```bash
./scripts/build_macos_app.sh
ditto -x -k "dist/Math-Harness-0.11.0-macOS-arm64.zip" /tmp/math-harness-beta
open "/tmp/math-harness-beta/Math Harness.app"
```

构建脚本使用 PyInstaller 把 Python 解释器、SymPy、FastAPI 和模型客户端放进 App，
用户机器不需要另外安装 Python 或 `uv`。产物是包含 `.app` 的 ZIP；默认使用 ad-hoc
签名，只适合本机测试。签名在 `/tmp` 完成，是为了避免 iCloud/File Provider 管理的
Documents 工作区自动附加 Finder 元数据并破坏签名。

Developer ID 分发：

```bash
export MATH_HARNESS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export MATH_HARNESS_NOTARY_PROFILE="math-harness-notary"
./scripts/package_macos_dmg.sh
```

公证需要完整 Xcode，以及事先通过 `xcrun notarytool store-credentials` 创建的钥匙串
配置。未设置 `MATH_HARNESS_NOTARY_PROFILE` 时只生成未公证 DMG。

## 本地数据与安全边界

- 数据存放在 `~/Library/Application Support/Math Harness/`；每个工作区继续
  使用独立 SQLite 数据库。
- MiMo API Key 只保存在 macOS Keychain。App 每次启动时把它放入子进程环境，不写入
  数据库、日志或项目文件。
- App 让后台只监听 `127.0.0.1` 的随机端口，并生成每次启动都不同的 256 位令牌。
  所有 HTTP 路由都要求 Bearer Token。
- 后台日志位于 `~/Library/Application Support/Math Harness/Logs/backend.log`。

`Resources/MathHarnessApp.entitlements` 是后续 Mac App Store 沙箱工作的起点，当前直接
分发构建不会启用它。沙箱版还需要给嵌入的 Python helper 配置继承权限并完成商店审核，
不能只给主 App 打开沙箱开关。

## v0.11.0 记忆工坊

- 同一工作区可以创建和切换多个持久对话；不同工作区的消息、摘要和知识继续物理隔离。
- “普通聊天”直接调用已配置的 MiMo；离线模式会诚实提示能力边界，同时仍保存消息。
- “验算求解”把候选解、验证状态、求解记录和待审核知识草稿关联到同一个对话回合。
- 助手消息落库后只持久化后台任务，记忆模型的延迟或失败不阻塞对话响应。
- 提取器只读新增用户原文；候选需通过原文证据、敏感信息和数学内容检查。
- 记忆页支持搜索、分类、置顶、编辑、归档、恢复、来源会话跳转、健康状态和显式历史回填。
- 软记忆使用 SQLite FTS5 和中文 2/3 字符 n-gram；每轮注入不超过 3,000 字符。
- 软记忆只调整交流方式，不能代替验证器、方法卡或数学事实。
- 旧对话不会在升级时自动发给模型；历史回填必须二次确认。

## 数据操作

- 工具栏“数据”菜单可以选择 JSON/JSONL 题库。App 先显示总数、可导入数、重复数与逐项
  错误；只有整批合法时才能确认，确认后的数据库写入使用一个 SQLite 事务。
- 单批最多 500 题、5 MB。默认把所有题放入待复核区，并使用免费本地规则提炼方法。
  “保留 reviewed 标记”与“当前提炼器”均为显式选项；后者可能为每道新题产生一次模型请求。
- 工作区备份扩展名为 `.mathharness`，包含一致性 SQLite 快照、应用版本、记录数与 SHA-256
  清单。恢复前会验证 ZIP 结构、大小、校验和、SQLite 完整性、外键和工作区隔离。
- 恢复始终创建名称带“（恢复）”的新工作区并重绑全部内部引用，不覆盖原工作区。
  备份包不包含 MiMo API Key；密钥仍只存在 macOS Keychain。

## 当前 Beta 边界

- 支持工作区创建/切换、历史求解、自然语言目标建议与人工确认、对话自动记忆、例题草稿
  编辑/重新校验、例题与方法草稿复核，以及方法卡查看/废弃。只有独立验证通过的当前
  修订可以由人工确认晋级。
- 目标建议稿不会自动求解：App 先填入表达式、变量、参数、假设、趋近点、方向、模式和
  余项阶数，用户检查后点击“确认并验算”。MiMo 不可用时退回本地规则；无法可靠识别时
  仍允许手填或明确继续非结构化对话。
- 草稿编辑保存前会保留旧版本，保存后重新运行验证与方法提炼。验证失败但尚未人工驳回
  的自动草稿继续显示在待复核区，不会成为不可见的死数据。
- 选择 MiMo 后默认同时使用 MiMo 提炼新方法；可在设置中关闭“求解后使用 MiMo 提炼
  方法”，以避免额外请求并退回内置规则模板。
- 现有 CLI `.math_harness/` 数据不会自动迁移；App 使用 Application Support 中的新数据
  根目录。正式迁移工具将在稳定版前补齐。
- 数学表达式暂以可选择的等宽文本展示；离线 LaTeX 排版留到后续版本。
- 对话回复尚未流式显示；跨对话记忆只覆盖用户背景与偏好，不是模型权重微调。
- 打包脚本当前构建本机架构。首个公开 macOS 构建以 Apple Silicon 为目标，Universal 2
  和自动更新在后续版本处理。
- 自动目标整理仍是前台请求；软记忆提取已改为可恢复的后台任务，最多重试三次。
- 对话记忆会让 App 新建的数据自然进入复核队列；旧 CLI `.math_harness/` 数据仍不会
  自动搬入 Application Support。
