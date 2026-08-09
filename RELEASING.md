# Release process

本项目从 `v0.2.0` 起保留可复现的 Git 标签和 GitHub Release。

## 发布新版本

1. 同步更新 `pyproject.toml`、`uv.lock`、Python API、Swift App、`Info.plist`、README
   和协议检查中的版本号。
2. 在 `.github/release-notes/vX.Y.Z.md` 编写版本说明。
3. 运行本地验证：

   ```bash
   uv sync --frozen --no-editable --extra dev --extra llm
   uv run --no-editable ruff format --check .
   uv run --no-editable ruff check .
   uv run --no-editable pytest
   uv run --no-editable math-harness-loop \
     --data-root /tmp/math-harness-release-loop --check
   uv run --no-editable math-harness-attribution \
     --max-mislabel-rate 0.0 --min-coverage 0.6
   uv run --no-editable math-harness-cross-eval \
     --data-root /tmp/math-harness-release-cross --check
   uv run --no-editable math-harness-mutation --check
   uv build
   swift format lint --recursive --strict \
     macos/MathHarnessApp/Package.swift macos/MathHarnessApp/Sources
   swift build --package-path macos/MathHarnessApp
   swift run --package-path macos/MathHarnessApp MathHarnessCoreChecks
   ./scripts/build_macos_app.sh
   ./scripts/test_macos_bundle.sh
   MATH_HARNESS_SKIP_APP_BUILD=true ./scripts/package_macos_dmg.sh
   ```

4. 合并版本提交到 `main`。
5. 创建并推送带注释标签：

   ```bash
   git tag -a vX.Y.Z -m "Math Harness vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```

pull request 与 `main` 推送由 `.github/workflows/ci.yml` 验证。标签触发
`.github/workflows/release.yml`：Python 与 macOS 两组任务分别执行完整门禁并暂存 wheel、
source distribution、Apple Silicon App ZIP 和 DMG；只有两组都成功后，`publish` 任务才
创建公开 GitHub Release 并一次性上传全部产物。GitHub 同时为每个标签提供标准源码 ZIP
和 tar.gz 归档。
