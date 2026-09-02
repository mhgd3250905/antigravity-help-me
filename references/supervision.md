# 原生 Agy 进程监督

## 启动前

1. 记录 task id、绝对 TASK.md、MODE/执行配置、绝对 workspace、Git 基线、模型、
   `--add-dir`、实际 `--mode`（plan/accept-edits/省略）、schema、timeout 和授权。
2. 确认本次技能没有另一个仍在运行的 Agy 进程。
3. 确认 `--json-schema` 是 Agy 可读的绝对路径，prompt 只含固定协议和受限的
   TASK.md 绝对路径，没有 TASK.md 正文或用户输入。
4. 默认让 Agy 从目标 workspace 启动，并同时传入 `--add-dir <ABS_WORKSPACE>`；
   不把 `--add-dir` 误认为 cwd 切换。
5. `REVIEW` 不默认加 `--dangerously-skip-permissions`；`CHANGE` 只有在可信
   workspace、用户授权写/命令且 headless 确实需要时才加。
6. 若 TASK.md 声明 exact Agy 工具名，重复传入 reducer 的 `--required-tool`；不要
   用“联网”等语义词替代工具名。
7. reducer 必须传 `--execution-profile`；显式 resume/project 还要传
   `--expected-conversation`/`--expected-project`；权限模式需要硬门禁时传当前版本已
   确认的 `--expected-permission-mode`，不能只在自然语言中声称核对过。
8. 每次 Agy 启动都显式传 `--effort high`。技能固定模型为
   `gemini-3.7-flash-high`；Agy 1.1.22 对该模型只接受省略 `--effort` 或匹配的
   `high`，`low`/`medium` 会产生 model selection conflict。若需要控制成本，由用户
   显式提供的读取/检查 allowlist、prompt-level 工具调用预算或停止条件控制（未显式提供
   时不注入默认限制）；启动前从 `agy --help` 确认该 flag 和取值仍受当前版本支持。

## 运行中

- 通过宿主 Agent 的内置终端启动一个前台 Agy 进程；宿主工具返回 session id 时
  持续等待同一 session，不重新启动 Agy。
- `--output-format stream-json` 的原始 NDJSON 必须通过
  `scripts/agy_stream_reducer.py` 或等价的本地 reducer；不要直接显示给主模型。
- reducer 只展示 `init`、阶段变化、warning/block、低频 heartbeat 和 final；默认
  75 秒限频、最多约 12 条、约 2 KiB。heartbeat 由独立 timer 产生，不依赖 stdin
  持续有事件。工具计数按 `conversation_id + step_index + tool_name`（或明确的
  tool-call id）去重，因此统计的是 unique invocation 而非 stream event；没有稳定
  identity 的兼容 envelope 按每次观察保守计数，绝不按工具名合并不同调用。未显式
  提供 tool_budget 时，工具计数仅供观察，不构成 fail-closed 或停机条件。文本增量
  和完整 tool output 丢弃。
- raw stdout 可保存为任务目录中的有界 `stream.ndjson`，compact 最新状态可保存
  为 `state.json`；两者在宿主上下文之外。stderr 单独落盘。
- 管道必须独立保存 Agy producer 与 reducer 两个退出码；Agy 非零时，即使 reducer
  已收到合法 final 也不能验收。运行前删除任务目录中的旧退出码文件。
- 仅在用户显式提供时，TASK.md 才注入读取/检查 allowlist、每个工具的调用预算或额外停止条件；
  默认不强加任何预算或 allowlist。这些是 prompt-level 约束，不是 Agy CLI 的硬门禁：当用户显式配置了
  预算时，宿主仍须从 reducer 事件、raw/state 和实际工作树独立核对，不能把模型遵守预算当作事实证明。
- Agy CLI 1.1.22 没有 `--max-turns`；仅在用户显式提供 tool_budget 时，宿主才根据 compact
  supervision 判断是否超限并停止当前 session 或创建窄范围返修任务。未显式提供预算时，工具计数仅用于
  可观测性，宿主不得自行发明上限，也不得因调用次数停止 Agy 或创建返修任务。
- timeout、心跳或暂时没有输出不等于失败。超过 Agy `--print-timeout` 加宿主宽限
  后才按超时处理；只停止本次技能启动且已确认身份的精确 session/PID。

## init 与 workspace/project

`init.cwd` 的规范化绝对路径必须与预期 workspace 完全一致。Agy 1.1.22 没有暴露
可依赖的 added-dir 证明字段；不要递归扫描任意 metadata 代替绑定证据。reducer
无法证明时输出 `protocol_error`，即使模型声称任务完成也不可验收。

若指定 required tools，`init.tools` 必须存在且包含每个 exact name；否则 compact
init 立即报告 `available_tools`/`missing_tools`，并在终态 fail-closed 为
`protocol_error`，让宿主尽早停止当前 session。

保存 `init` 的 cwd、project（若提供）和 conversation id。续接前逐项核对 task
id、MODE、授权、绝对 workspace、`--add-dir`、project 和原 conversation；project
未暴露或不一致时使用新 conversation 或停止，不能静默沿用固定的
`default-cli-project`。不要用 project 名称猜路径。reducer 会核对 execution profile、
实际 `/plan` expansion，以及显式提供的 expected conversation/project；不符即
`protocol_error`。CHANGE 的 accept-edits argv 和授权仍由宿主依据命令记录复核。

发布 readiness 的检查顺序由宿主控制：先物化 `VERSION`、README、`SECURITY.md` 等
当前版本文件，再启动 readiness gate。后续 tag/push 不属于实现审查的当前 blocker；
若文件尚未物化，应先结束当前审查、由宿主补齐并创建新的窄 TASK.md。

## JSON 分流与终态

Agy 的 `stream-json` 终态通常是 `type=result`，应包含 `status` 和由
`--json-schema` 约束的 `structured_output`。reducer 独立再次检查：

- `status` 异常、缺 final、缺 `structured_output`、字段缺失/多余、task id 不符或
  schema 类型错误：`protocol_error`；
- response 恰好为裸 `BLOCKED`：`protocol_error`，不当作有原因的阻塞；
- 结构化 `outcome=completed`：compact final 为 `protocol=completed`，仍需宿主独立
  检查任务产物和证据；
- 结构化 `outcome=blocked`：reason、missing、next_steps、evidence 均非空时为
  `protocol=blocked`；它表示可解释的任务阻塞，不表示成功。

compact final 只可携带受限 summary/证据和最多三个近期事件，不携带 response 原文、
文本 delta 或 tool output。终态拥有独立预算优先级：进度事件先停，合法 `blocked`
必须保留 `protocol`、`outcome`、`reason`、`missing`、`next_steps`、`evidence`，
合法 `completed` 必须保留 `protocol`、`outcome`、`summary`、`evidence`。细节被
压缩时添加 `truncated=true`，不得以 `output_budget_exceeded` 覆盖真实终态。退出码
0 只代表收到了结构化 completed/blocked；退出码 2 代表协议失败，任何情况下都不能
只凭退出码自动验收。

若 stdout 有非 JSON 前缀，reducer 应把它视为 malformed stream 并停止验收；不要
把前缀或 stderr 原样回灌模型。Python 不可用时改用 `--output-format json` 的
final-only 降级，详见 [compatibility.md](compatibility.md)。

## 返修与验收

返修 TASK.md 只写已确认缺陷、违反的契约、预期行为和需要重跑的检查；相同精确
失败不原样重试。MODE、目标、授权或环境变化必须新 conversation；独立验收必须
新 conversation。

最终需同时检查 compact final、Agy 与 reducer 两个退出码、实际工作树/Git diff、
TASK.md 要求和逐项证据，并区分用户已有改动、任务临时文件和 Agy 新增变化。
`SUCCESS`、conversation id、文件存在或非空 response 都不是完成证明。
