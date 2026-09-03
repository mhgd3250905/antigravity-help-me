# antigravity-help-me

在主 Agent 里调用本机 Antigravity CLI (`agy`) 跑 Gemini。

## 用

发给你的 Agent 一句话：

```
安装这个技能：https://github.com/mhgd3250905/antigravity-help-me
```

手动：

```bash
git clone https://github.com/mhgd3250905/antigravity-help-me.git ~/.codex/skills/antigravity-help-me
```

## 示例

技能名放在任务前或后都行：

- **默认模型**（`gemini-3.8-flash-high`）：
  ```text
  $antigravity-help-me 审查登录流程的输入校验
  审查登录流程的输入校验 $antigravity-help-me
  ```
- **显式指定模型**：
  ```text
  $antigravity-help-me 用 gemini-3.8-flash-medium 审查登录流程的输入校验
  审查登录流程的输入校验，使用模型 gemini-3.7-flash-high $antigravity-help-me
  ```

### 宿主 CLI 调用

宿主 Agent 可向 `doctor`/`run`/`batch` 传递 `--model`（省略时默认 `gemini-3.8-flash-high`；从 `-high`/`-medium`/`-low` 自动推导 `--effort`）：

```bash
# 检查环境并验证模型可用性
python scripts/agy_helper.py doctor --json
python scripts/agy_helper.py doctor --json --model gemini-3.8-flash-medium

# 单任务派发（显式模型）
python scripts/agy_helper.py run --preset review-local --model gemini-3.8-flash-medium --request-stdin

# 独立多任务派发（单批次统一模型，支持 1..3 路并发；这里使用默认模型）
python scripts/agy_helper.py batch --request-stdin
```

请求 stdin 是一行 UTF-8 JSON；格式化多行请求请改用 `--request-file`。最小四字段模板、macOS/Windows 调用方式、preset 额外字段和错误说明见 [SKILL.md](SKILL.md) 与 [references/fast-path.md](references/fast-path.md)。
`run`/`batch` 的五个 preset 默认传入 `--dangerously-skip-permissions` 跳过 CLI 交互确认，任务授权和结构化验收不变；`doctor` 只做能力探测。

### 单任务与多任务 Batch

- **单任务（`run`）**：单个明确任务（如审查、修改或验证），由 helper 运行单路 Agy 并返回结构化结果。支持 `--model`（默认 `gemini-3.8-flash-high`，从 `-high`/`-medium`/`-low` 后缀自动推导 `--effort`）。
- **独立多任务（`batch`）**：多个互不依赖的独立任务，由宿主 Agent 在单次调用中提交 `batch`（支持 `1..3` 路并发，默认 3），内部统一调度，无需手动开启多个终端。支持 `--model` 统一指定批次模型（一个批次使用一个模型）。
- **并发与写保护**：只读任务或不同工作区任务可并发执行；同一或重叠工作区的写任务（`change`/`repair`）自动互斥，避免重叠并发写入。

## 前提

`agy` 已安装并登录（`agy --version` 能跑通）。技能不负责安装和登录。

---

## English

Call your local Antigravity CLI (`agy`) to run Gemini from inside your main Agent.

**Usage** — send your Agent one line:

```
Install this skill: https://github.com/mhgd3250905/antigravity-help-me
```

Manually:

```bash
git clone https://github.com/mhgd3250905/antigravity-help-me.git ~/.codex/skills/antigravity-help-me
```

**Example** — skill name before or after the task, either works:

- **Default model** (`gemini-3.8-flash-high`):
  ```text
  $antigravity-help-me review input validation in the login flow
  review input validation in the login flow $antigravity-help-me
  ```
- **Explicit model**:
  ```text
  $antigravity-help-me use gemini-3.8-flash-medium to review input validation in the login flow
  review input validation in the login flow with model gemini-3.7-flash-high $antigravity-help-me
  ```

### Host CLI Invocations

The host agent can pass `--model` to `doctor`, `run`, or `batch` (omitting it defaults to `gemini-3.8-flash-high`; `--effort` is derived automatically from `-high`/`-medium`/`-low`):

```bash
# Probe environment and verify model availability
python scripts/agy_helper.py doctor --json
python scripts/agy_helper.py doctor --json --model gemini-3.8-flash-medium

# Single-task dispatch with an explicit model
python scripts/agy_helper.py run --preset review-local --model gemini-3.8-flash-medium --request-stdin

# Independent multi-task dispatch (one model per batch, 1..3 parallel lanes; default model shown)
python scripts/agy_helper.py batch --request-stdin
```

Requests sent through stdin must be one UTF-8 JSON line; use `--request-file` for formatted multi-line JSON. See [SKILL.md](SKILL.md) and [references/fast-path.md](references/fast-path.md) for the minimal four-field template, macOS/Windows invocations, preset requirements, and validation errors.
All five `run`/`batch` presets pass `--dangerously-skip-permissions` by default to skip CLI confirmation prompts, while task authorization and structured acceptance remain unchanged; `doctor` only probes capabilities.

### Single Task vs. Batch

- **Single task (`run`)**: Use for a single scoped task (review, edit, or verification). The helper manages one Agy lane and returns structured results. Supports `--model` (defaults to `gemini-3.8-flash-high` with deterministically derived `--effort`).
- **Independent multi-task (`batch`)**: When dispatching multiple independent jobs, the host agent submits a `batch` in a single invocation with bounded concurrency (1..3, default 3), without requiring manually opened multiple terminals. Supports `--model` for the entire batch (one model per batch).
- **Concurrency & write safety**: Read-only tasks and non-overlapping workspaces run concurrently; write tasks (`change`/`repair`) on overlapping workspaces are automatically serialized to avoid parallel overlapping writes.

**Prerequisite** — `agy` is installed and signed in (`agy --version` works). The skill does not install or sign in for you.
