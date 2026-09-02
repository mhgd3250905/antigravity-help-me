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

```
$antigravity-help-me 审查登录流程的输入校验

审查登录流程的输入校验 $antigravity-help-me
```

### 单任务与多任务 Batch

- **单任务（`run`）**：单个明确任务（如审查、修改或验证），由 helper 运行单路 Agy 并返回结构化结果。
- **独立多任务（`batch`）**：多个互不依赖的独立任务，由宿主 Agent 在单次调用中提交 `batch`（支持 `1..3` 路并发，默认 3），内部统一调度，无需手动开启多个终端。
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

```
$antigravity-help-me review input validation in the login flow

review input validation in the login flow $antigravity-help-me
```

### Single Task vs. Batch

- **Single task (`run`)**: Use for a single scoped task (review, edit, or verification). The helper manages one Agy lane and returns structured results.
- **Independent multi-task (`batch`)**: When dispatching multiple independent jobs, the host agent submits a `batch` in a single invocation with bounded concurrency (1..3, default 3), without requiring manually opened multiple terminals.
- **Concurrency & write safety**: Read-only tasks and non-overlapping workspaces run concurrently; write tasks (`change`/`repair`) on overlapping workspaces are automatically serialized to avoid parallel overlapping writes.

**Prerequisite** — `agy` is installed and signed in (`agy --version` works). The skill does not install or sign in for you.
