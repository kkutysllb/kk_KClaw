---
sidebar_position: 2
title: "在 Mac 上运行本地 LLM"
description: "在 macOS 上使用 llama.cpp 或 MLX 设置本地 OpenAI 兼容 LLM 服务器，包括模型选择、内存优化和 Apple Silicon 上的真实基准测试"
---

# 在 Mac 上运行本地 LLM

本指南带您完成在 macOS 上运行具有 OpenAI 兼容 API 的本地 LLM 服务器。您获得完全隐私、零 API 成本以及在 Apple Silicon 上令人惊讶的良好性能。

我们涵盖两个后端：

| 后端 | 安装 | 最擅长 | 格式 |
|---------|---------|---------|--------|
| **llama.cpp** | `brew install llama.cpp` | 首个令牌最快时间、量化 KV 缓存以降低内存 | GGUF |
| **omlx** | [omlx.ai](https://omlx.ai) | 令牌生成最快、原生 Metal 优化 | MLX (safetensors) |

两者都暴露 OpenAI 兼容的 `/v1/chat/completions` 端点。KClaw 与任一者配合使用——只需将其指向 `http://localhost:8080` 或 `http://localhost:8000`。

:::info 仅 Apple Silicon
本指南针对配备 Apple Silicon（M1 及更高版本）的 Mac。Intel Mac 可以使用 llama.cpp 但没有 GPU 加速——预期会明显更慢。
:::

---

## 选择模型

对于入门，我们推荐 **Qwen3.5-9B**——这是一个强大的推理模型，在量化的情况下可以舒适地容纳在 8GB+ 统一内存中。

| 变体 | 磁盘大小 | 所需 RAM（128K 上下文） | 后端 |
|---------|-------------|---------------------------|---------|
| Qwen3.5-9B-Q4_K_M (GGUF) | 5.3 GB | ~10 GB（量化 KV 缓存） | llama.cpp |
| Qwen3.5-9B-mlx-lm-mxfp4 (MLX) | ~5 GB | ~12 GB | omlx |

**内存经验法则：** 模型大小 + KV 缓存。Q4 9B 模型约为 5 GB。128K 上下文的 KV 缓存在 Q4 量化下增加约 4-5 GB。使用默认（f16）KV 缓存，这会膨胀到约 16 GB。llama.cpp 中的量化 KV 缓存标志是内存受限系统的关键技巧。

对于更大的模型（27B、35B），您需要 32 GB+ 统一内存。9B 是 8-16 GB 机器的最佳选择。

---

## 选项 A：llama.cpp

llama.cpp 是最具可移植性的本地 LLM 运行时。在 macOS 上它开箱即用地使用 Metal 进行 GPU 加速。

### 安装

```bash
brew install llama.cpp
```

这为您提供了全局的 `llama-server` 命令。

### 下载模型

您需要一个 GGUF 格式的模型。最简单的来源是通过 `huggingface-cli` 从 Hugging Face 下载：

```bash
brew install huggingface-cli
```

然后下载：

```bash
huggingface-cli download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/models
```

:::tip 门控模型
Hugging Face 上的一些模型需要身份验证。如果遇到 401 或 404 错误，先运行 `huggingface-cli login`。
:::

### 启动服务器

```bash
llama-server -m ~/models/Qwen3.5-9B-Q4_K_M.gguf \
  -ngl 99 \
  -c 131072 \
  -np 1 \
  -fa on \
  --cache-type-k q4_0 \
  --cache-type-v q4_0 \
  --host 0.0.0.0
```

以下是每个标志的作用：

| 标志 | 目的 |
|------|---------|
| `-ngl 99` | 将所有层卸载到 GPU（Metal）。使用高数字以确保没有任何东西留在 CPU 上。 |
| `-c 131072` | 上下文窗口大小（128K tokens）。如果内存不足，请减少此值。 |
| `-np 1` | 并行槽数量。保持为 1 以供单用户使用——更多槽会分割您的内存预算。 |
| `-fa on` | Flash attention。减少内存使用并加速长上下文推理。 |
| `--cache-type-k q4_0` | 将键缓存量化为 4 位。**这是大内存节省器。** |
| `--cache-type-v q4_0` | 将值缓存量化为 4 位。与上述一起，与 f16 相比将 KV 缓存内存减少约 75%。 |
| `--host 0.0.0.0` | 监听所有接口。如果不需要网络访问，请使用 `127.0.0.1`。 |

当您看到以下内容时服务器已就绪：

```
main: server is listening on http://0.0.0.0:8080
srv  update_slots: all slots are idle
```

### 内存受限系统的内存优化

`--cache-type-k q4_0 --cache-type-v q4_0` 标志是内存受限系统最重要的优化。以下是 128K 上下文的影响：

| KV 缓存类型 | KV 缓存内存（128K 上下文，9B 模型） |
|---------------|--------------------------------------|
| f16（默认） | ~16 GB |
| q8_0 | ~8 GB |
| **q4_0** | **~4 GB** |

在 8GB Mac 上，使用 `q4_0` KV 缓存并将上下文减少到 `-c 32768`（32K）。在 16GB 上，您可以舒适地使用 128K 上下文。在 32GB+ 上，您可以运行更大的模型或多个并行槽。

如果您仍然内存不足，首先减少上下文大小（`-c`），然后尝试更小的量化（Q3_K_M 而不是 Q4_K_M）。

### 测试它

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.5-9B-Q4_K_M.gguf",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 50
  }' | jq .choices[0].message.content
```

### 获取模型名称

如果您忘记模型名称，请查询模型端点：

```bash
curl -s http://localhost:8080/v1/models | jq '.data[].id'
```

---

## 选项 B：通过 omlx 的 MLX

[omlx](https://omlx.ai) 是一个 macOS 原生应用，管理和提供 MLX 模型。MLX 是 Apple 自己的机器学习框架，针对 Apple Silicon 的统一内存架构进行了专门优化。

### 安装

从 [omlx.ai](https://omlx.ai) 下载并安装。它提供模型管理的 GUI 和内置服务器。

### 下载模型

使用 omlx 应用浏览和下载模型。搜索 `Qwen3.5-9B-mlx-lm-mxfp4` 并下载。模型本地存储（通常在 `~/.omlx/models/`）。

### 启动服务器

omlx 默认在 `http://127.0.0.1:8000` 上提供模型。从应用 UI 开始服务，或使用 CLI（如果有）。

### 测试它

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.5-9B-mlx-lm-mxfp4",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 50
  }' | jq .choices[0].message.content
```

### 列出可用模型

omlx 可以同时提供多个模型：

```bash
curl -s http://127.0.0.1:8000/v1/models | jq '.data[].id'
```

---

## 基准测试：llama.cpp vs MLX

两个后端在同一台机器（Apple M5 Max，128 GB 统一内存）上测试，运行相同模型（Qwen3.5-9B）在可比量化级别（GGUF 的 Q4_K_M，MLX 的 mxfp4）。五个不同提示，各三次运行，后端顺序测试以避免资源争用。

### 结果

| 指标 | llama.cpp (Q4_K_M) | MLX (mxfp4) | 胜者 |
|--------|-------------------|-------------|--------|
| **TTFT（平均）** | **67 ms** | 289 ms | llama.cpp（4.3x 更快） |
| **TTFT（p50）** | **66 ms** | 286 ms | llama.cpp（4.3x 更快） |
| **生成（平均）** | 70 tok/s | **96 tok/s** | MLX（快 37%） |
| **生成（p50）** | 70 tok/s | **96 tok/s** | MLX（快 37%） |
| **总时间（512 tokens）** | 7.3s | **5.5s** | MLX（快 25%） |

### 这意味着什么

- **llama.cpp** 在提示处理方面表现出色——其 flash attention + 量化 KV 缓存管道让您在大约 66ms 内获得第一个令牌。如果您正在构建感知响应重要的交互式应用程序（聊天机器人、自动完成），这是一个有意义的优势。

- **MLX** 一旦开始生成令牌，速度约快 37%。对于批处理工作负载、长表单生成或任何总完成时间比初始延迟更重要的任务，MLX 更快完成。

- 两个后端都**非常一致**——跨运行的差异可以忽略不计。您可以依赖这些数字。

### 您应该选择哪个？

| 使用场景 | 建议 |
|----------|---------------|
| 交互式聊天、低延迟工具 | llama.cpp |
| 长表单生成、批量处理 | MLX (omlx) |
| 内存受限（8-16 GB） | llama.cpp（量化 KV 缓存无与伦比） |
| 同时提供多个模型 | omlx（内置多模型支持） |
| 最大兼容性（也支持 Linux） | llama.cpp |

---

## 连接到 KClaw

一旦您的本地服务器运行：

```bash
kclaw model
```

选择 **Custom endpoint** 并按照提示操作。它会询问基础 URL 和模型名称——使用您在上面设置的后端的值。
