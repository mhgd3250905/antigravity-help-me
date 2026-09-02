# Quickstart

可直接复制的命令。每条命令里的路径都换成你自己的绝对路径。

Copy-paste-ready commands. Replace every path with your own absolute path.

[简体中文](#简体中文) · [English](#english)

---

## 简体中文

### 0. 安装

```bash
# Codex
git clone https://github.com/mhgd3250905/antigravity-help-me.git ~/.codex/skills/antigravity-help-me

# 其他 Agent
git clone https://github.com/mhgd3250905/antigravity-help-me.git /path/to/your/agent/skills/antigravity-help-me
```

后文用 `<SKILL>` 表示这个目录的绝对路径。Windows 上用 `py -3` 代替 `python`。

### 1. 体检

```bash
python <SKILL>/scripts/agy_helper.py doctor --json
```

输出 `status=ready` 就可以继续；`blocked` 按输出里的 `next_action` 处理（通常是 `agy` 没装 / 没登录，或模型列表里没有 `gemini-3.7-flash-high`）。

### 2. 派活

把请求写成 JSON 文件，字段最少四个：

```json
{
  "workspace": "/abs/path/to/project",
  "goal": "审查登录流程的输入校验",
  "scope": ["src/auth/**", "tests/auth/**"],
  "acceptance": ["每条发现都要给出文件和证据"]
}
```

然后按任务类型选一个 preset：

```bash
# 本地只读审查
python <SKILL>/scripts/agy_helper.py run --preset review-local    --request-file /abs/path/request.json

# 需要联网 / 外部工具的审查
python <SKILL>/scripts/agy_helper.py run --preset review-external --request-file /abs/path/request.json

# 改代码（请求里必须带 allowed_changes 和 authorization）
python <SKILL>/scripts/agy_helper.py run --preset change          --request-file /abs/path/request.json

# 针对上一次失败返修（再带 parent_task_id 和 failure）
python <SKILL>/scripts/agy_helper.py run --preset repair          --request-file /abs/path/request.json

# 独立验收（请求里必须带 subject）
python <SKILL>/scripts/agy_helper.py run --preset verify          --request-file /abs/path/request.json
```

改代码的请求长这样：

```json
{
  "workspace": "/abs/path/to/project",
  "goal": "给登录接口补上输入校验",
  "scope": ["src/auth/routes.py", "tests/auth/"],
  "acceptance": ["新增校验分支", "测试通过"],
  "allowed_changes": ["src/auth/routes.py", "tests/auth/"],
  "authorization": "允许修改上述文件并运行测试"
}
```

也可以用 stdin 一行传入（正文不进命令行参数，不受终端长度限制）：

```bash
python <SKILL>/scripts/agy_helper.py run --preset review-local --request-stdin
```

### 3. 看结果

运行时主会话只收到压缩后的阶段事件，最后一条是终态：

- `completed` — 进入验收：核对 TASK.md 的要求、实际文件 / Git 改动和逐项证据。
- `blocked` — 不是成功。按 `next_steps` 补输入、创建窄范围 `repair` 任务，或收手。
- `protocol_error` — 协议层出错（结果不合 schema、缺终态等），不可验收。

`verify` 类型的 `completed` 还会带 `verdict: pass|fail`。

产物在 `<workspace>/.antigravity-help-me/tasks/<task-id>/`：

```text
TASK.md            任务契约（派发后不可变）
state.json         最新压缩状态
stream.ndjson      原始流日志（有界，不进主会话）
evidence/          证据附件
```

需要时可用 `--run-timeout` 调整本次调度上限（1–7200 秒，默认 1860 秒）。

### 手工兜底

helper 不可用时才走手工流程，见 [references/stream-supervision.md](references/stream-supervision.md)：

```bash
agy --add-dir <ABS_WORKSPACE> --mode plan --model gemini-3.7-flash-high --effort high \
    --output-format stream-json --json-schema <SKILL>/references/result-schema.json \
    --print-timeout 1800s -p '<FIXED_PROMPT>'
```

原始输出必须经 `scripts/agy_stream_reducer.py` 压缩后再看，不要把裸流倒进主会话。

---

## English

### 0. Install

```bash
# Codex
git clone https://github.com/mhgd3250905/antigravity-help-me.git ~/.codex/skills/antigravity-help-me

# Any other agent
git clone https://github.com/mhgd3250905/antigravity-help-me.git /path/to/your/agent/skills/antigravity-help-me
```

Below, `<SKILL>` is the absolute path to that directory. On Windows, use `py -3` instead of `python`.

### 1. Doctor

```bash
python <SKILL>/scripts/agy_helper.py doctor --json
```

`status=ready` means you're good to go. If `blocked`, follow `next_action` in the output — usually `agy` missing or unauthenticated, or `gemini-3.7-flash-high` absent from the model list.

### 2. Dispatch

Write the request as a JSON file with at least four fields:

```json
{
  "workspace": "/abs/path/to/project",
  "goal": "Review input validation in the login flow",
  "scope": ["src/auth/**", "tests/auth/**"],
  "acceptance": ["Every finding cites a file and evidence"]
}
```

Then pick a preset for your task type:

```bash
# Read-only local review
python <SKILL>/scripts/agy_helper.py run --preset review-local    --request-file /abs/path/request.json

# Review needing web / external tools
python <SKILL>/scripts/agy_helper.py run --preset review-external --request-file /abs/path/request.json

# Change code (request must include allowed_changes and authorization)
python <SKILL>/scripts/agy_helper.py run --preset change          --request-file /abs/path/request.json

# Rework a previous failure (also needs parent_task_id and failure)
python <SKILL>/scripts/agy_helper.py run --preset repair          --request-file /abs/path/request.json

# Independent verification (request must include subject)
python <SKILL>/scripts/agy_helper.py run --preset verify          --request-file /abs/path/request.json
```

A change request looks like this:

```json
{
  "workspace": "/abs/path/to/project",
  "goal": "Add input validation to the login endpoint",
  "scope": ["src/auth/routes.py", "tests/auth/"],
  "acceptance": ["Validation branch added", "Tests pass"],
  "allowed_changes": ["src/auth/routes.py", "tests/auth/"],
  "authorization": "Allowed to modify the files above and run tests"
}
```

You can also pass one JSON line via stdin (the body never enters argv, so terminal length limits don't apply):

```bash
python <SKILL>/scripts/agy_helper.py run --preset review-local --request-stdin
```

### 3. Read the result

While running, the host session only receives compact phase events. The last one is the terminal state:

- `completed` — proceed to verification: check the TASK.md requirements, the actual file / Git changes, and each piece of evidence.
- `blocked` — not a success. Follow `next_steps` to supply input, open a narrow `repair` task, or stop.
- `protocol_error` — protocol-level failure (result violates the schema, missing terminal state, etc.). Not acceptable.

A completed `verify` also carries `verdict: pass|fail`.

Artifacts live in `<workspace>/.antigravity-help-me/tasks/<task-id>/`:

```text
TASK.md            task contract (immutable after dispatch)
state.json         latest compact state
stream.ndjson      raw stream log (bounded, never enters the host session)
evidence/          evidence attachments
```

Use `--run-timeout` to change the dispatch ceiling (1–7200 seconds, default 1860).

### Manual fallback

Only if the helper is unavailable. See [references/stream-supervision.md](references/stream-supervision.md):

```bash
agy --add-dir <ABS_WORKSPACE> --mode plan --model gemini-3.7-flash-high --effort high \
    --output-format stream-json --json-schema <SKILL>/references/result-schema.json \
    --print-timeout 1800s -p '<FIXED_PROMPT>'
```

Raw output must go through `scripts/agy_stream_reducer.py` first — never dump the bare stream into the host session.
