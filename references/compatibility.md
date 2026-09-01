# 宿主与系统兼容边界

核心协议由 `agy`、宿主内置终端和工作区任务文件组成。技能发现、终端 session 和授权 UI 由各宿主实现。

## 宿主最低契约

宿主 Agent 必须能够：

- 在指定 cwd 运行命令；
- 安全传递独立 argv，或正确引用固定短 prompt；
- 捕获 stdout、stderr，并分别保存 Agy producer 与 reducer 的退出码；
- 等待超过普通单次工具时限的进程，或轮询同一个持久 session；
- 读写当前工作区 TASK.md，并检查任务产生的文件变化。
- 若启用实时监督，还要能把 Agy 的 stdout NDJSON 通过 stdin 交给本地 reducer，
  同时把 raw stdout/stderr 落盘到任务目录而不是展示给模型。

缺少其中任一项时，不能声称完整支持；可以报告限制，但不要引入外部终端窗口作为替代。

## Agy CLI 绑定

- 调度命令从目标 workspace 的绝对路径启动，并显式传入
  `--add-dir <ABS_WORKSPACE>`；`--add-dir` 不会改变 Agy 的 `cwd`。
- `init.cwd` 的规范化绝对路径必须与期望 workspace 完全一致；Agy 1.1.22 不提供
  可依赖的 added-dir 证明字段，无法证明时停止，不以任意 metadata、模型文字或
  退出码代替绑定证明。
- 执行配置必须明确三态：本地只读 `REVIEW_LOCAL` 使用 `--mode plan`，外部/工具型
  `REVIEW_EXTERNAL` 省略 `--mode`，`CHANGE` 使用 `--mode accept-edits`。reducer
  调用同时传入与 TASK.md 一致的 `--task-mode REVIEW|CHANGE` 和相应
  `--execution-profile`；resume/project 再传 expected id 供 init 校验。
- Agy 启动命令三个执行配置都必须显式传 `--effort high`。技能固定模型为
  `gemini-3.7-flash-high`；Agy 1.1.22 对该模型只接受省略 `--effort` 或匹配的
  `high`，`low`/`medium` 会产生 model selection conflict。成本由 prompt-level
  allowlist、工具调用预算和停止条件控制。兼容性探测的 `agy --help` 必须确认
  `--effort low|medium|high`；不支持时停止，不静默省略或替换该策略。
- 不指定 project 时不要假设当前 cwd 就是 Agy project；若使用 `--project`，保存
  并在续接前核对明确的 project id。全局选项放在子命令之前，例如
  `agy --output-format json models`。

## Windows

- 在 Codex 等桌面宿主的内置终端中直接调用 `agy`；不要用 `Start-Process`、`wt.exe`、`cmd /k` 或 `powershell -NoExit`。
- 使用受限 task id 和固定单引号 prompt，避免 PowerShell 展开 `$()`、反引号和变量。
- 不跨 PowerShell、cmd 和批处理脚本拼接删除、移动或取消进程命令。
- 只取消宿主返回的当前 session 或精确 PID；不要 `Stop-Process -Name agy`。
- 可用 `py -3` 或已探测成功的 `python` 启动本技能 reducer。PowerShell 直接把
  Agy stdout 管道给 reducer；不要把中间 NDJSON 打印到宿主模型日志。

## macOS 与 Linux

- 使用宿主内置 shell，固定 prompt 用单引号；task id 不含空格和 shell 元字符。
- 不把 TASK.md 正文放进 argv、环境变量或命令替换。
- reducer 使用 Python 3 标准库，可用 `python3` 或已探测成功的 `python` 启动；不
  依赖 Node、jq 或全局 shell profile。

## Python 缺失时的降级

Python 不是安装前提。宿主应先探测 `python --version`/Windows 的
`py -3 --version`，再探测 `<ABS_REDUCER> --help`。若不可用：

1. 不把 `--output-format stream-json` 直接接入主会话；
2. 使用同一 `--json-schema` 和工作区绑定，改用 `--output-format json`；
3. stdout/stderr 只落盘，主会话仅解析最终 JSON，执行同样的 task id、字段和
   `completed`/`blocked` 验收门禁。

这一路径没有实时 phase/heartbeat，属于明确降级；不得为启用 reducer 自动安装
运行时、修改 PATH、登录或扩大权限。

## 产品适配

- Codex 可读取 `agents/openai.yaml`，并用 unified terminal session 监督长进程。
- 其他支持 Agent Skills 的宿主可复用 SKILL.md 与 references，忽略 Codex 专属元数据。
- 只把 Markdown 当说明但不支持技能发现的 Agent，需要用户按该产品方式安装或显式附加本目录；这不改变原生 Agy 调用协议。
