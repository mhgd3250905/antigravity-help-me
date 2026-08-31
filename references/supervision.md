# 原生 Agy 进程监督

## 启动前

1. 记录 task id、TASK.md、MODE、工作目录、Git 基线、模型、timeout 和授权。
2. 确认本次技能没有另一个仍在运行的 Agy 进程。
3. 确认命令 prompt 只含固定启动语和受限相对路径，没有 TASK.md 正文或用户输入。

## 运行中

- 通过宿主 Agent 的内置终端启动一个前台 Agy 进程；宿主工具返回 session id 时持续等待同一 session。
- timeout、心跳或暂时没有输出不等于失败。超过 Agy timeout 加宿主宽限后才按超时处理。
- 只停止本次技能启动且已确认身份的进程；不按进程名批量终止全局 Agy、Node、PowerShell 或终端。
- 新用户输入替换任务时，先终止或等待当前进程达到终态，再创建新任务。

## JSON 分流

Agy 的 `--output-format json` 应返回一个 JSON 对象。stdout 有非 JSON 前缀时，只从第一个 `{` 开始尝试解析；仍不可解析则保留 stdout、stderr 和退出码并停止。

- `status` 含 `TIMEOUT`：失败；报告 conversation id（若有），缩小任务或由用户决定增加 timeout。
- 非 `SUCCESS` 且 response 为空：失败；逐字保留 error/stderr，不擅自换模型或权限档。
- 非 `SUCCESS` 但 response 完整：标记 `done_with_warnings`；结果可供主会话检查，但不能自动通过。
- `SUCCESS` 但 response 为空、usage 缺失或总 token 为 0：视为假成功，不进入验收通过。
- `SUCCESS`、response 非空且 usage 有效：记录 conversation id，进入主会话验收。

stderr 中出现 OAuth、认证、localhost bind 或 `operation not permitted` 时，先判断宿主沙箱是否隐藏 Agy 凭据或本地端口。技能不能绕过宿主安全边界。

## 返修

返修 TASK.md 只写已确认缺陷、违反的契约、预期行为和需要重跑的检查。相同错误不原样重试；先修复任务、环境或权限原因。

