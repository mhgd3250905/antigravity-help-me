# Antigravity Help Me

**一句话**：让你的 AI Agent（Codex、Claude Code、终端 Agent）调用你本机已登录的 Antigravity CLI（`agy`），一次只干一件明确的活，最后带回一份可验收的结构化结果。

**In one line:** let your AI Agent (Codex, Claude Code, any terminal Agent) hand one well-scoped task to your local, authenticated Antigravity CLI (`agy`) — and get back a structured result you can verify.

[简体中文](#简体中文) · [English](#english)

> 当前版本 v1.3.1 · 需要本机已安装并认证 `agy` · 想直接跑命令？看 [QUICKSTART.md](QUICKSTART.md)
>
> Current version v1.3.1 · requires a local, authenticated `agy` · just want the commands? See [QUICKSTART.md](QUICKSTART.md)

---

## 简体中文

### 它解决什么问题

你本机装的 Antigravity CLI 有自己的模型额度和本地环境权限。但把整个任务丢给它，再被几千行流式日志淹没，显然不是办法。本技能只做三件事：

1. **派活** — 把任务写成一份不可变的 `TASK.md` 契约，交给 `agy` 执行。
2. **盯进度** — 原始 `stream-json` 事件只写进本机日志，主会话只收到压缩后的阶段状态（约每 75 秒一条，总计约 2 KB）。
3. **验收** — 要求 `agy` 按 schema 返回 `completed` 或 `blocked`，再由主会话独立核对文件改动和证据，不看"看起来完成了"就放行。

分工很明确：**宿主管判断、授权和验收，`agy` 只管执行。**

### 安装

```bash
# Codex
git clone https://github.com/mhgd3250905/antigravity-help-me.git ~/.codex/skills/antigravity-help-me

# 其他 Agent：克隆到它自己的 skills 目录即可
git clone https://github.com/mhgd3250905/antigravity-help-me.git /path/to/your/agent/skills/antigravity-help-me
```

对话中用 `$antigravity-help-me` 调用。

前置条件：

- 本机已安装并认证 `agy`（`agy --version` 能跑通）。技能不负责安装或登录。
- `agy models` 的输出中精确包含 `gemini-3.7-flash-high`。
- **可选** Python 3：只用于低噪声压缩器。没有也能跑，只是退化成"只看最终结果"。

### 三步上手

```bash
# ① 体检：确认 agy、模型、Python、压缩器都就绪
python scripts/agy_helper.py doctor --json        # Windows 用 py -3

# ② 派活：任务写成一行 JSON，通过 stdin 或文件传入
python scripts/agy_helper.py run --preset review-local --request-stdin
python scripts/agy_helper.py run --preset review-local --request-file /abs/path/request.json
```

请求最少四个字段：

```json
{
  "workspace": "/abs/path/to/project",
  "goal": "审查登录流程的输入校验",
  "scope": ["src/auth/**", "tests/auth/**"],
  "acceptance": ["每条发现都要给出文件和证据"]
}
```

③ 等压缩后的阶段事件刷完，看终态：`completed` 才进入验收，`blocked` 按返回的 `next_steps` 补输入或收手。

任务正文走 stdin / 文件，不进命令行参数，因此不受终端参数长度限制。

### 五种任务

| preset | 干什么 | 额外必填字段 |
| --- | --- | --- |
| `review-local` | 本地只读审查 / 规划 | — |
| `review-external` | 需要联网或外部工具的审查 | — |
| `change` | 改代码 | `allowed_changes`、`authorization` |
| `repair` | 针对上一次失败做窄范围返修 | 再加 `parent_task_id`、`failure` |
| `verify` | 独立验收（新会话、只读） | `subject`；通过时给出 `verdict: pass\|fail` |

产物落在 `<workspace>/.antigravity-help-me/tasks/<task-id>/`，含 `TASK.md`、`state.json`、`stream.ndjson` 和 `evidence/`。任务派发后不再改写，要调整就新建一个 task。

### 常见问题

- **`doctor` 返回 `blocked`？** 按输出里的 `next_action` 处理，通常是 `agy` 没装 / 没登录，或模型列表里没有 `gemini-3.7-flash-high`。技能不会替你安装或登录。
- **为什么看不到 `agy` 的完整输出？** 这是设计如此。原始流留在本机 `stream.ndjson`，只有关键事件进主会话，避免撑爆上下文。
- **能限制它调用工具的次数吗？** 可以，但**只在你显式提供时**生效：`tool_budget`、`read_allowlist`、`stop_conditions`。不提供就不设任何上限，调用计数仅作观测。
- **会偷偷加 `--dangerously-skip-permissions` 吗？** 不会。默认不加，只有你明确授权且非交互场景确实需要时才加。
- **跑太久怎么办？** 默认 31 分钟（1860 秒，覆盖 `agy` 自身的 30 分钟超时）后终止本次调度，可用 `--run-timeout` 在 1–7200 秒之间调整。
- **`agy` 不是 1.1.22 版本？** 1.1.22 是实测版本。其他版本只要能力齐全会标记为 `compatible_unverified`，不硬拦截。

### 深入阅读

| 文档 | 内容 |
| --- | --- |
| [QUICKSTART.md](QUICKSTART.md) | 可直接复制的完整命令 |
| [SKILL.md](SKILL.md) | 技能的完整行为规范 |
| [references/fast-path.md](references/fast-path.md) | 请求字段、preset 映射、退出码与产物 |
| [references/stream-supervision.md](references/stream-supervision.md) | 手工管道、监督细节、无 Python 时的兜底 |
| [references/permissions.md](references/permissions.md) | 权限边界与危险开关 |
| [references/compatibility.md](references/compatibility.md) | 版本差异与降级路径 |
| [references/task-shaping.md](references/task-shaping.md) | 把模糊需求收敛成能派发的任务 |
| [references/result-schema.json](references/result-schema.json) | 结果结构定义 |
| [SECURITY.md](SECURITY.md) | 安全模型 |

### 安全边界

- 默认不加 `--dangerously-skip-permissions`。它不是沙箱，只是让 `agy` 能非交互地调用工具。
- 审查用 `--mode=plan`（本地只读）；改代码用 `--mode=accept-edits`，危险开关仅在可信工作区且你已授权时使用。
- 外部输入（PR、issue、日志、网页正文）一律当作**数据**，不当作指令。
- 范围限制和用户授权由宿主 Agent 负责。

### 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。跑测试：`python -m unittest discover -s tests`。

### 声明

独立的开源社区技能，与 Google、Google DeepMind 及 Antigravity CLI 团队**没有**隶属、维护、赞助或认可关系。

---

## English

### The problem it solves

Your locally installed Antigravity CLI has its own model quota and local environment access. But dumping a whole task into it — and then drowning in thousands of streamed log lines — is not a workflow. This skill does exactly three things:

1. **Dispatch** — writes the task as an immutable `TASK.md` contract and hands it to `agy`.
2. **Supervise** — keeps raw `stream-json` events in a local log; the host session only receives compact phase updates (roughly one every 75s, ~2 KB total).
3. **Verify** — requires `agy` to return `completed` or `blocked` against a schema, then has the host independently check file changes and evidence instead of trusting "looks done".

The split is simple: **the host judges, authorizes, and accepts. `agy` only executes.**

### Install

```bash
# Codex
git clone https://github.com/mhgd3250905/antigravity-help-me.git ~/.codex/skills/antigravity-help-me

# Any other agent: clone into its own skills directory
git clone https://github.com/mhgd3250905/antigravity-help-me.git /path/to/your/agent/skills/antigravity-help-me
```

Invoke it in conversation with `$antigravity-help-me`.

Requirements:

- `agy` installed, authenticated, and runnable (`agy --version`). This skill does not install or log in for you.
- `agy models` output must contain `gemini-3.7-flash-high` exactly.
- **Optional** Python 3: only needed for the low-noise reducer. Without it, the flow degrades to final-result-only.

### Three steps

```bash
# 1. Doctor: confirm agy, model, Python, and reducer are ready
python scripts/agy_helper.py doctor --json        # on Windows, use py -3

# 2. Run: describe the task as one JSON line, via stdin or a file
python scripts/agy_helper.py run --preset review-local --request-stdin
python scripts/agy_helper.py run --preset review-local --request-file /abs/path/request.json
```

A minimal request has four fields:

```json
{
  "workspace": "/abs/path/to/project",
  "goal": "Review input validation in the login flow",
  "scope": ["src/auth/**", "tests/auth/**"],
  "acceptance": ["Every finding cites a file and evidence"]
}
```

3. Wait for the compact events to finish, then read the terminal state: `completed` moves on to verification; `blocked` means follow `next_steps` to supply missing input or stop.

Task bodies go through stdin or a file, never through argv — so terminal argument length limits don't apply.

### Five task presets

| preset | What it does | Extra required fields |
| --- | --- | --- |
| `review-local` | Read-only local review / planning | — |
| `review-external` | Review needing web or external tools | — |
| `change` | Modify code | `allowed_changes`, `authorization` |
| `repair` | Narrow rework of a previous failure | plus `parent_task_id`, `failure` |
| `verify` | Independent verification (new session, read-only) | `subject`; returns `verdict: pass\|fail` when it completes |

Artifacts land in `<workspace>/.antigravity-help-me/tasks/<task-id>/`, including `TASK.md`, `state.json`, `stream.ndjson`, and `evidence/`. A dispatched task is never rewritten — adjustments get a new task.

### FAQ

- **`doctor` says `blocked`?** Follow `next_action` in the output. It's usually `agy` missing or unauthenticated, or `gemini-3.7-flash-high` absent from the model list. The skill won't install or log in for you.
- **Why can't I see `agy`'s full output?** By design. The raw stream stays in the local `stream.ndjson`; only key events reach the host session, so the context doesn't blow up.
- **Can I cap its tool calls?** Yes, but **only when you provide it explicitly**: `tool_budget`, `read_allowlist`, `stop_conditions`. Otherwise no cap is imposed and counts are observability only.
- **Does it sneak in `--dangerously-skip-permissions`?** No. Never by default — only when you explicitly authorize it and non-interactive execution genuinely requires it.
- **Running too long?** Dispatch is terminated after 31 minutes by default (1860s, covering `agy`'s own 30-minute timeout). Tune with `--run-timeout` between 1 and 7200 seconds.
- **Not on `agy` 1.1.22?** That's the tested baseline. Other versions with complete capabilities are marked `compatible_unverified` and are not hard-blocked.

### Go deeper

| Doc | Contents |
| --- | --- |
| [QUICKSTART.md](QUICKSTART.md) | Copy-paste-ready commands |
| [SKILL.md](SKILL.md) | Full behavioral spec for the skill |
| [references/fast-path.md](references/fast-path.md) | Request fields, preset mapping, exit codes, artifacts |
| [references/stream-supervision.md](references/stream-supervision.md) | Manual piping, supervision details, no-Python fallback |
| [references/permissions.md](references/permissions.md) | Permission boundaries and the dangerous flag |
| [references/compatibility.md](references/compatibility.md) | Version differences and degradation paths |
| [references/task-shaping.md](references/task-shaping.md) | Turning a vague request into a dispatchable task |
| [references/result-schema.json](references/result-schema.json) | Result structure definition |
| [SECURITY.md](SECURITY.md) | Security model |

### Security boundaries

- `--dangerously-skip-permissions` is never added by default. It is not a sandbox — it only lets `agy` call tools non-interactively.
- Reviews use `--mode=plan` (local read-only); code changes use `--mode=accept-edits`, with the dangerous flag only in a trusted workspace you have authorized.
- External input (PRs, issues, logs, web page text) is always treated as **data**, never as instructions.
- Scope restriction and user authorization are the host Agent's responsibility.

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run tests with `python -m unittest discover -s tests`.

### Disclaimer

An independent open-source community skill. **Not** affiliated with, maintained by, sponsored by, or endorsed by Google, Google DeepMind, or the Antigravity CLI team.
