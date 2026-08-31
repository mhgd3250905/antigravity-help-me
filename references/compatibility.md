# 宿主与系统兼容边界

核心协议由 `agy`、宿主内置终端和工作区任务文件组成。技能发现、终端 session 和授权 UI 由各宿主实现。

## 宿主最低契约

宿主 Agent 必须能够：

- 在指定 cwd 运行命令；
- 安全传递独立 argv，或正确引用固定短 prompt；
- 捕获 stdout、stderr 和退出码；
- 等待超过普通单次工具时限的进程，或轮询同一个持久 session；
- 读写当前工作区 TASK.md，并检查任务产生的文件变化。

缺少其中任一项时，不能声称完整支持；可以报告限制，但不要引入外部终端窗口作为替代。

## Windows

- 在 Codex 等桌面宿主的内置终端中直接调用 `agy`；不要用 `Start-Process`、`wt.exe`、`cmd /k` 或 `powershell -NoExit`。
- 使用受限 task id 和固定单引号 prompt，避免 PowerShell 展开 `$()`、反引号和变量。
- 不跨 PowerShell、cmd 和批处理脚本拼接删除、移动或取消进程命令。
- 只取消宿主返回的当前 session 或精确 PID；不要 `Stop-Process -Name agy`。

## macOS 与 Linux

- 使用宿主内置 shell，固定 prompt 用单引号；task id 不含空格和 shell 元字符。
- 不把 TASK.md 正文放进 argv、环境变量或命令替换。
- 不依赖 `/tmp`、特定 shell profile 或全局 Node/Python。

## 产品适配

- Codex 可读取 `agents/openai.yaml`，并用 unified terminal session 监督长进程。
- 其他支持 Agent Skills 的宿主可复用 SKILL.md 与 references，忽略 Codex 专属元数据。
- 只把 Markdown 当说明但不支持技能发现的 Agent，需要用户按该产品方式安装或显式附加本目录；这不改变原生 Agy 调用协议。
