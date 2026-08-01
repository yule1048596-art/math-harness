# Release process

本项目从 `v0.2.0` 起保留可复现的 Git 标签和 GitHub Release。

## 发布新版本

1. 更新 `pyproject.toml`、API 版本和 README。
2. 在 `.github/release-notes/vX.Y.Z.md` 编写版本说明。
3. 运行本地验证：

   ```bash
   uv sync --no-editable --extra dev
   uv run --no-editable ruff format --check .
   uv run --no-editable ruff check .
   uv run --no-editable pytest
   uv build
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

标签触发 `.github/workflows/release.yml`。工作流会重新安装锁定依赖、执行检查、
构建 wheel、source distribution 和 Apple Silicon macOS App ZIP/DMG，然后创建 GitHub
Release 并上传构建产物。GitHub 同时为每个标签提供标准源码 ZIP 和 tar.gz 归档。
