# KClaw Agent 的 Homebrew 打包说明

使用 `packaging/homebrew/kclaw.rb` 作为 tap 或 `homebrew-core` 的起点。

关键选择：
- 稳定版本应针对每个 GitHub 发布附加的 semver 命名的 sdist 资产，而不是 CalVer tag 的 tarball。
- `faster-whisper` 现在位于 `voice` extra 中，这可以将仅 wheel 的传递依赖项排除在基础 Homebrew formula 之外。
- 包装器导出 `KCLAW_BUNDLED_SKILLS`、`KCLAW_OPTIONAL_SKILLS` 和 `KCLAW_MANAGED=homebrew`，以便打包安装保留运行时资源并由 Homebrew 处理升级。

典型更新流程：
1. 更新 formula 的 `url`、`version` 和 `sha256`。
2. 使用 `brew update-python-resources --print-only kclaw` 刷新 Python 资源。
3. 保留 `ignore_packages: %w[certifi cryptography pydantic]`。
4. 验证 `brew audit --new --strict kclaw` 和 `brew test kclaw`。
