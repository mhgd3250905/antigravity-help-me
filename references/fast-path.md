# Fast path：命令优先派发

`scripts/agy_helper.py` 把固定的环境判断和命令装配收敛为 bridge-first 的统一入口：
核心是做好主会话向 Agy 转发完整任务、实时监督、保存证据并把结构化结果带回主会话。
宿主 Agent 仍负责目标、授权、范围和最终验收；helper 负责确定性校验、任务契约和
进程监督，不安装、升级、登录或扩大 Agy 权限，默认不附加微观工具限制或调用预算。

## 1. 先做 doctor

从本仓库运行：

```text
python <ABS_REPO>\scripts\agy_helper.py doctor --json
```

Windows 也可以用已验证的 `py -3`。`doctor` 只读检查：

- `agy` 是否存在且可运行；
- `agy --version`：版本 `1.1.22` 标记为 `tested`；其他版本若能力齐全则 `status=ready` 且 `compatibility=compatible_unverified`，不硬阻断；
- `agy --help` 是否包含工作区、mode、print、model、effort、stream/json、schema、
  timeout、conversation 和按需权限参数；
- `agy --output-format json models` 是否成功且精确包含
  `gemini-3.7-flash-high`；
- 当前 Python 以及本仓库 `scripts\agy_stream_reducer.py --help` 是否可用。

输出是单行稳定 JSON。`doctor` 仅在 agy 缺失或不可运行、必需 CLI 能力缺失、固定模型不可用、Python/reducer 不可用时 blocked。`status=ready` 时即可进入 `run`；若有阻断按 `next_action` 处理。退出码是稳定分类码，不能用安装、更新或登录来自动修复。

## 2. 用 run preset

请求正文优先通过 stdin 发送一行 UTF-8 JSON：

```text
python <ABS_REPO>\scripts\agy_helper.py run --preset review-local --request-stdin
```

stdin framing 是“一行一个 JSON 对象”；helper 只读取有界的一行，不等待 EOF，
因此宿主可以在长任务中保持管道打开。当 `--request-stdin` 连接交互式 TTY 时，helper 会在读取前临时关闭终端输入 echo，并在成功、解析失败和异常路径中可靠恢复（兼容 Windows Console/ConPTY 与 POSIX）；若环境无法切换 echo 则不破坏输入，在此类终端中建议使用 `--request-file`。正文不会进入 Windows argv、环境变量或
固定启动 prompt。需要文件输入时使用：

```text
python <ABS_REPO>\scripts\agy_helper.py run --preset review-local --request-file <ABS_REQUEST_JSON>
```

两种输入必须二选一。普通最小请求只包含：

```json
{
  "workspace": "E:\\project",
  "goal": "审查登录流程",
  "scope": ["src/auth/**", "tests/auth/**"],
  "acceptance": ["每项发现包含文件和证据"]
}
```

默认 `required_tools` 为空，不生成或执行默认工具调用预算、读取 allowlist、每工具限制或多余停止条款。只有用户显式提供的 constraints（如 `required_tools`、`tool_budget`、`read_allowlist`、`stop_conditions`）才进入任务契约。
若显式提供 `tool_budget`，可包含 `max_total_calls`、`max_calls_per_tool`、`max_updates`（传给 reducer）和 `stop_when_exhausted`；数值必须是有界正整数，布尔值只能用于 `stop_when_exhausted`。未显式提供 `tool_budget` 时，工具计数仅用于可观测性，宿主和 helper 均不施加默认上限，不构成 fail-closed 或停机条件，不得因调用次数停止 Agy 或触发返修。

Preset 映射：

| preset | execution profile | task mode | 额外字段/规则 |
| --- | --- | --- | --- |
| `review-local` | `REVIEW_LOCAL` | `REVIEW` | 本地只读 |
| `review-external` | `REVIEW_EXTERNAL` | `REVIEW` | 外部工具审查 |
| `change` | `CHANGE` | `CHANGE` | 必须有非空 `allowed_changes` 与 `authorization` |
| `repair` | `CHANGE` | `CHANGE` | 还必须有 `parent_task_id`、`failure`、`allowed_changes`、`authorization`，新 conversation |
| `verify` | `REVIEW_LOCAL` | `REVIEW` | 必须有 `subject`，只读、新 conversation；`outcome=completed` 时必须返回 `verdict: pass|fail`，blocked 保留 blocked 语义 |

`run` 会在目标 workspace 创建唯一的
`.antigravity-help-me/tasks/<task-id>/TASK.md`。task id 只使用小写字母、数字和
连字符，长度不超过 48；目标已经存在时拒绝覆盖。TASK.md 仍是不可变追溯产物，
包含 profile/mode、绝对路径、用户显式约束、授权和禁止项。返修创建新 task，不原地改写前序任务。

## 3. 输出与退出语义

`run` 的 stdout 逐行转发 reducer 的 compact JSON 事件，并在连续 75 秒没有可见 compact 输出且 producer 仍运行时发出 helper 自身的低频 heartbeat（不依赖 reducer 的 2 KiB progress 预算，终态后停止，数量有界且不包含 raw/tool output）；调度结束时输出一个 `event=run` 汇总。Agy raw stream、tool output 和长 response 只保存在任务目录：

```text
TASK.md
launch.json
state.json
stream.ndjson
producer-stderr.log
reducer-stderr.log
producer-exit.txt
reducer-exit.txt
run.json
```

helper 使用 argv 数组从 workspace 启动 Agy，同时传入 `--add-dir`、固定模型
`gemini-3.7-flash-high`、`--effort high`、schema、`stream-json` 和 print timeout。
`REVIEW_LOCAL` 使用 `--mode plan`，`REVIEW_EXTERNAL` 省略 mode，`CHANGE` 使用
`--mode accept-edits`。默认不传 `--dangerously-skip-permissions`。

`run` 默认把本次精确 producer/reducer 调度限制在 1860 秒（覆盖 Agy 默认的
1800 秒 print timeout），也可用 `--run-timeout` 调整到 1–7200 秒。超时时先对
本次子进程 `terminate`，仍未退出才 `kill`；不按进程名终止。reducer 已发终态后，
producer 只获得 5 秒 post-final grace；超出后 summary 标记
`producer_grace_timeout` 并终止本次 producer。producer 非零、reducer 非零、超时
或协议错误都不能当作成功。

`result-schema.json` 的 `verdict` 是可选字段，因此旧任务结果继续有效；helper
只在 `verify` 启动 reducer 的 `--require-verdict`。completed verify 要求 `pass`
或 `fail`，而合法 blocked verify 不要求 verdict（携带 verdict 会被拒绝为矛盾的
终态），并在 compact final/state/run 汇总中保留 completed verdict。

## 手工 fallback

只有 helper 不可用或需要调试底层 transport 时，才阅读
[stream-supervision.md](stream-supervision.md)、[compatibility.md](compatibility.md)
和 [permissions.md](permissions.md) 中的手工 TASK/argv/reducer 流程。手工流程
仍必须遵循绝对 workspace、`--add-dir`、固定 schema、正确 profile/mode、producer
与 reducer 分离退出码、raw log 留存和独立验收规则。
