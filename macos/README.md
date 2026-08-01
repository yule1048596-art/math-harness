# Math Harness for macOS

v0.6.0 引入第一个原生 macOS 客户端。SwiftUI 负责工作区、对话、验证状态和方法卡审阅；
现有 Python/SymPy 服务作为独立子进程运行，因此数学内核无需重写，计算异常也不会直接
卡住界面。

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
ditto -x -k "dist/Math-Harness-0.6.0-macOS-arm64.zip" /tmp/math-harness-alpha
open "/tmp/math-harness-alpha/Math Harness.app"
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

## v0.6.0 Alpha 边界

- 目前支持工作区创建/切换、历史求解、结构化验证目标、方法卡查看与人工晋级/废弃。
- 现有 CLI `.math_harness/` 数据不会自动迁移；App 使用 Application Support 中的新数据
  根目录。正式迁移工具将在稳定版前补齐。
- 数学表达式暂以可选择的等宽文本展示；离线 LaTeX 排版留到后续版本。
- 打包脚本当前构建本机架构。首个公开 macOS 构建以 Apple Silicon 为目标，Universal 2
  和自动更新在后续版本处理。
- 这一版尚未把一次成功对话自动转成待审知识案例，成长闭环仍需显式摄取接口；这将是
  v0.7.0 的核心工作。
