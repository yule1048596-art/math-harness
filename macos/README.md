# Math Harness for macOS

v0.20.0 是面向 macOS 14+ / Apple Silicon 的原生 SwiftUI App。界面通过仅监听
`127.0.0.1` 的本地 Python helper 使用 Math Harness 核心能力；发布包已经嵌入 Python、
SymPy、FastAPI 和 OpenAI-compatible 客户端，最终用户不需要安装开发环境。

普通对话支持流式显示。可信度检查只在完整回复结束后运行；供应商中途断开时，已收到的
正文会保留并显示“生成中断”，但不会获得可信度、形成知识草稿或进入成长闭环。

## 开发运行

只安装 Apple Command Line Tools 也可以编译和运行：

```bash
uv sync --frozen --no-editable --extra dev --extra llm
./scripts/run_macos_app.sh
```

开发模式会从仓库通过 `uv run math-harness-server` 自动启动后台。也可以连接已经运行的
服务：

```bash
MATH_HARNESS_BACKEND_URL=http://127.0.0.1:8000 \
  ./scripts/run_macos_app.sh
```

Swift 格式、编译和核心协议自检：

```bash
swift format lint --recursive --strict \
  macos/MathHarnessApp/Package.swift \
  macos/MathHarnessApp/Sources
swift build --package-path macos/MathHarnessApp
swift run --package-path macos/MathHarnessApp MathHarnessCoreChecks
```

CoreChecks 也覆盖旧设置的事务式迁移：Keychain 操作失败时不得写完成标记，下次启动必须
可以重试。

## 构建独立 App

```bash
./scripts/build_macos_app.sh
./scripts/test_macos_bundle.sh
MATH_HARNESS_SKIP_APP_BUILD=true ./scripts/package_macos_dmg.sh
```

当前版本的默认产物为：

```text
dist/Math-Harness-0.20.0-macOS-arm64.zip
dist/Math-Harness-0.20.0-macOS-arm64.dmg
```

`build_macos_app.sh` 使用 PyInstaller 打包 Python helper，再编译 SwiftUI App、生成图标、
签名并验证 ZIP。签名和归档在 `/tmp` 的临时目录完成，避免 Documents/iCloud 工作区的
Finder 元数据破坏签名。`test_macos_bundle.sh` 会从 ZIP 解包并验证鉴权、工作区隔离、
对话、记忆、导入、备份恢复和求解链路；DMG 脚本还会检查 App 与 `/Applications` 拖放
入口是否存在。

## Developer ID 与公证

默认构建使用 ad-hoc 签名，只适合测试和当前公开 Beta 分发。Developer ID 构建：

```bash
export MATH_HARNESS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export MATH_HARNESS_NOTARY_PROFILE="math-harness-notary"
./scripts/package_macos_dmg.sh
```

公证需要完整 Xcode，以及事先通过 `xcrun notarytool store-credentials` 创建的钥匙串
配置。未设置 `MATH_HARNESS_NOTARY_PROFILE` 时只生成未公证 DMG。

## 数据与安全边界

- 用户数据位于 `~/Library/Application Support/Math Harness/`；每个工作区使用独立
  SQLite 数据库。
- API Key 只保存在 macOS Keychain。App 启动 helper 时通过进程环境传递，不写入数据库、
  日志、备份或仓库。
- helper 只监听随机的 `127.0.0.1` 端口，并为每次启动生成独立的 256 位 Bearer Token。
- 后台日志位于 `~/Library/Application Support/Math Harness/Logs/backend.log`。
- 不完整的流式回答会永久保存中断元数据；错误文本会移除 provider 密钥。
- 软记忆只影响交流方式。数学结论必须经过检查、知识草稿和人工复核边界。

`Resources/MathHarnessApp.entitlements` 是未来沙箱发行的起点，当前直接分发构建不启用。
沙箱版还需要为嵌入的 Python helper 配置继承权限并完成商店审核。

## 当前 Beta 边界

- 公共包仅提供 Apple Silicon，最低 macOS 14，尚无 Universal 2。
- 默认 ad-hoc 签名，尚未完成 Developer ID 公证和自动更新。
- 数学表达式仍以可选择文本显示，尚无原生离线 LaTeX 排版。
- App 与旧 CLI 的 `.math_harness/` 数据目录不自动合并；工作区可通过
  `.mathharness` 备份显式迁移。
- 这是单机本地应用架构，不是面向不受信任用户的公网多租户服务。
