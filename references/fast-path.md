# Fast path：命令优先派发

`scripts/agy_helper.py` 把固定的环境判断和命令装配收敛为 bridge-first 的统一入口：
核心是做好主会话向 Agy 转发完整任务、实时监督、保存证据并把结构化结果带回主会话。
宿主 Agent 仍负责目标、授权、范围和最终验收；helper 负责确定性校验、任务契约和
进程监督，不安装、升级、登录，不改全局权限配置或扩大任务授权，默认不附加微观工具限制或调用预算。

## 1. 先做 doctor

从本仓库运行：

```text
python <ABS_REPO>\scripts\agy_helper.py doctor --json [--model <MODEL_ID>]
```

Windows 也可以用已验证的 `py -3`。`doctor` 只读检查：

- `agy` 是否存在且可运行；
- `agy --version`：版本 `1.1.24` 标记为 `tested`；其他版本若能力齐全则 `status=ready` 且 `compatibility=compatible_unverified`，不硬阻断；
- `agy --help` 是否包含工作区、mode、print、model、effort、stream/json、schema、
  timeout、conversation 和默认执行所需权限参数；
- `agy --output-format json models` 是否成功且精确包含
  选定模型（默认 `gemini-3.8-flash-high`，执行精确可用性检查）；
- 当前 Python 以及本仓库 `scripts\agy_stream_reducer.py --help` 是否可用。

输出是单行稳定 JSON。`doctor` 仅在 agy 缺失或不可运行、必需 CLI 能力缺失、选定模型不可用、Python/reducer 不可用或模型无法确定 effort 时 blocked/error。`status=ready` 时即可进入 `run`；若有阻断按 `next_action` 处理。退出码是稳定分类码，不能用安装、更新或登录来自动修复。

## 2. 用 run preset

请求正文优先通过 stdin 发送一行 UTF-8 JSON（支持 `--model`，默认 `gemini-3.8-flash-high`，由 `-high`/`-medium`/`-low` 后缀确定性推导 `--effort`）：

```text
python <ABS_REPO>\scripts\agy_helper.py run --preset review-local --request-stdin [--model <MODEL_ID>]
```

stdin framing 是“一行一个 JSON 对象”；helper 只读取有界的一行，不等待 EOF，
因此宿主可以在长任务中保持管道打开。当 `--request-stdin` 连接交互式 TTY 时，helper 会在读取前临时关闭终端输入 echo，并在成功、解析失败和异常路径中可靠恢复（兼容 Windows Console/ConPTY 与 POSIX）；若环境无法切换 echo 则不破坏输入，在此类终端中建议使用 `--request-file`。正文不会进入 Windows argv、环境变量或
固定启动 prompt。需要文件输入时使用：

```text
python <ABS_REPO>\scripts\agy_helper.py run --preset review-local --request-file <ABS_REQUEST_JSON> [--model <MODEL_ID>]
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

这是 helper 可直接运行的最小四字段请求。`workspace` 必须是已存在目录的绝对路径；`scope` 是相对 workspace
的路径/glob 或范围说明字符串列表，不是硬访问控制。`--request-stdin` 只读取一行 UTF-8 JSON；格式化多行 JSON
请改用 `--request-file <ABS_REQUEST_JSON>`。`task_id` 由 helper 生成，任务模式由 CLI `--preset` 选择；请求中的
`task_id`/`task_mode` 会保留兼容但只产生非阻断 warning，不会改变实际 preset 语义。

请求校验失败时 helper 保持 `event=error`、`status=error`、`code=20` 和 `message` 兼容，并额外返回一次汇总的
`errors` 数组；每项含 `path`、`expected`、`hint`、`message`，同时给出本技能输入文档指引。batch 会继续检查可判定
的其他 job，并把请求错误定位为 `jobs[0].request.scope` 这类完整路径。JSON 语法错误或根结构不是对象时可直接退出。

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

## 3. 用 batch 提交独立任务

需要一次提交多个独立任务时使用（支持 `--model` 统一指定批次模型，批次内所有 job 使用同一选定模型）：

```text
python <ABS_REPO>\scripts\agy_helper.py batch --request-stdin [--model <MODEL_ID>]
python <ABS_REPO>\scripts\agy_helper.py batch --request-file <ABS_BATCH_JSON> [--model <MODEL_ID>]
```

batch 输入是一个有界 UTF-8 JSON 对象。每项必须明确 `preset` 和原有 `request`；
`request` 的字段校验与 `run` 完全相同：

```json
{
  "batch_id": "batch-api-review",
  "max_parallel": 3,
  "jobs": [
    {
      "job_id": "auth-review",
      "preset": "review-local",
      "request": {
        "workspace": "E:\\project",
        "goal": "审查认证流程",
        "scope": ["src/auth/**"],
        "acceptance": ["每项结论包含文件和证据"]
      }
    },
    {
      "job_id": "api-review",
      "preset": "review-local",
      "request": {
        "workspace": "E:\\project-api",
        "goal": "审查 API 边界",
        "scope": ["src/api/**"],
        "acceptance": ["列出可复核发现"]
      }
    }
  ]
}
```

顶层 `batch_id`、每项 `job_id` 均可省略；省略时 helper 生成批次 ID，并按输入顺序生成
`job-001`、`job-002` 等 ID。ID 只使用小写字母、数字和连字符，长度不超过 48。
`max_parallel` 只能为整数 `1..3`，默认 `3`；命令行 `--run-timeout` 作为每个 lane
的独立 deadline，而不是整个 batch 的共享 deadline。helper 会先完整校验所有 job，
再只做一次 `doctor`，随后每个 job 使用独立 Agy、Reducer、task id、task 目录和日志。

工作区 admission 按规范化绝对路径判断相同、祖先或子孙关系：

| 任务组合 | 重叠 workspace | 不重叠 workspace |
| --- | --- | --- |
| `review-local`/`review-external`/`verify` + 只读任务 | 可共享 | 可并发 |
| 任一 `change`/`repair` + 读或写任务 | 互斥 | 可并发 |

调度器会扫描待运行队列，让不相关 workspace 继续运行；当重叠写任务等待时，新的同
workspace 读任务不会无限插队，因此写任务不会因读任务饥饿。一次调用内活跃 Agy
producer 永远不超过 `max_parallel`，第 4 个可运行 job 排队。这个上限只属于当前
helper 进程，不是跨进程或整机全局限制；独立终端/helper 调用不参加同一 admission。
只有真正独立的任务才可放入同一 batch；不要为填充 lane 拆分一个任务。resume 同一
conversation 仍必须串行，独立验收/返修仍使用新 conversation。

每个 lane 的 compact `init`、`phase`、`heartbeat`、`final`、`run` 事件都带 `batch_id` 和
`job_id`，输出由 helper 按 JSONL 行原子写出；最后一行是 `event=batch` 汇总，包含
按输入顺序排列的 `jobs` 以及 `jobs_completed`、`jobs_blocked`、`jobs_failed`、
`jobs_cancelled` 计数，并重复实际的 batch `exit_code`。批次状态和退出码如下：

- `completed`：全部 lane 为 `completed`，退出码 `0`；
- `blocked`：lane 只有 `completed`/`blocked`，至少一个是 `blocked`，退出码 `0`；
- `failed`：任一 lane 为 producer/reducer failure、timeout、协议错误或派发失败，退出码非零；
- `cancelled`：宿主取消后活动 lane 被精确终止、排队 lane 标记为 `cancelled`，退出码非零；
- `preflight_failed`：doctor 未通过，没有启动 lane，退出码沿用 doctor 分类码。

单 lane 失败、blocked 或 timeout 只影响该 lane；其他活动和排队 lane 继续按 admission
运行。取消时 helper 只终止本批已启动的精确 producer/reducer PID，不按进程名清理。

## 4. 输出与退出语义

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

helper 使用 argv 数组从 workspace 启动 Agy，同时传入 `--add-dir`、选定模型
（默认 `gemini-3.8-flash-high`）、推导的 `--effort`（默认 `high`）、默认的
`--dangerously-skip-permissions`、schema、`stream-json` 和 print timeout。
TASK.md、launch.json 以及 run/batch 汇总均记录选定 model 与 effort。
`REVIEW_LOCAL` 使用 `--mode plan`，`REVIEW_EXTERNAL` 省略 mode，`CHANGE` 使用
`--mode accept-edits`；五个 preset 默认都传入 `--dangerously-skip-permissions`，该 flag
仅免除 CLI 交互确认，不扩大 TASK.md 中的业务授权。`doctor` 只做探测，不传入该执行 flag。

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
