# 可选技能

由 kkutysllb 维护的**默认不激活**的官方技能。

这些技能随 kclaw 仓库一起发布，但不会在设置期间复制到
`~/.kclaw/skills/`。它们可通过技能中心发现：

```bash
kclaw skills browse               # 浏览所有技能，官方技能优先显示
kclaw skills browse --source official  # 仅浏览官方可选技能
kclaw skills search <query>       # 查找标记为 "official" 的可选技能
kclaw skills install <identifier> # 复制到 ~/.kclaw/skills/ 并激活
```

## 为什么是可选的？

一些技能有用但并非每个用户都需要：

- **小众集成** — 特定的付费服务、专业工具
- **实验性功能** — 有前景但尚未成熟
- **重量级依赖** — 需要大量设置（API 密钥、安装）

通过保持它们可选，我们保持默认技能集精简，同时为需要的用户提供精选、测试过的官方技能。
