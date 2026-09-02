# 把任务收敛成 Agy 擅长的一件事

Gemini Flash High 擅长快速完成确定任务，不代替主会话作开放式判断。

## 调度前必须确定

- 唯一可观察目标和交付物；
- 已知输入、仍有效证据和固定技术决策；
- 可检查或修改的路径与明确排除项；
- 目标 workspace、TASK.md 和关键输入的绝对路径；
- 成功证据、停止条件和授权边界。

缺失项会改变结果时由主会话先判断；无法安全推断时再问用户。

## batch 的独立性与工作区策略

只有彼此独立的任务才放入同一 `batch`；同一 conversation 的 resume 必须串行，不能
为了占满三条 lane 人为拆分一个任务。`review-local`、`review-external` 和 `verify`
属于只读任务，同一或祖先/子孙重叠 workspace 可以共享；`change` 和 `repair` 属于
写任务，与重叠读/写互斥，互不重叠 workspace 才可并发写。helper 的 `batch` admission
只约束当前一次调用，独立终端/helper 调用不共享这个上限。

调度时不因一个被锁 workspace 阻塞其他可运行 workspace；如果重叠写任务在等待，新的
同 workspace 读任务不能无限插队。单个 lane 的失败、blocked 或 timeout 只影响自身；
宿主取消时才统一终止本批活动 lane，并收束排队状态。

## 必须拆开的任务

“研究所有方案、选一个、实现、测试并决定是否上线”不是一件任务。拆成：

1. 有界事实摸排；
2. 主会话作选择；
3. 按既定方案实施；
4. 新 conversation 独立验收。

每个 TASK.md 只保留当前阶段需要的事实。后续任务引用已验收结论，不复制完整历史。

## 发布准备的顺序

发布相关动作由宿主会话持有，不能倒置 readiness gate：

1. 先由宿主物化当前版本要求的 `VERSION`、README、`SECURITY.md` 等发布文件；
2. 再启动只读 readiness/release gate，检查这些文件、测试和工作树状态；
3. readiness 通过后，才由宿主创建 tag、推送远端或执行其他发布动作。

如果当前 TASK.md 只是实现审查或代码审查，后续仍未执行的发布动作不是当前 blocker，
不得把“将来要创建 VERSION/README/tag”写成 Agy 的现状缺陷。需要同时改实现和发布文件
时拆成先物化发布文件、后 readiness 审查的两个窄任务；Agy 只能检查主会话已经提供
的事实和产物。

## 消除模糊表达

- “优化体验”改成具体用户路径、目标行为和验证方式。
- “全面研究”改成有限问题、证据入口和返回格式。
- “整理代码”改成具体缺陷、目标行为、文件范围和测试。
- “你决定”改成主会话已选方案；只允许 Agy 决定不影响契约的局部实现细节。
- 若用户显式要求约束范围或成本，可在任务中明确读取/检查 allowlist（精确路径或有限模式）、
  工具调用预算或停止条件；未显式提供时，默认不强加预算或 allowlist。Agy CLI 1.1.22
  没有 `--max-turns`，用户显式提供的 allowlist、工具预算和停止条件属于 prompt-level 约束；
  仅在显式配置预算时宿主才通过 compact supervision 判断超限并停止或创建窄范围返修任务。
  未显式提供预算时，工具计数仅供观察，宿主不得自行发明上限或停止任务。
- 返回形状固定为 schema 的 `task_id`、`outcome`、`summary`、`reason`、`missing`、
  `next_steps`、`evidence`；`blocked` 必须说明具体原因，禁止只返回裸 `BLOCKED`。

遇到缺证据、输出发散或擅自决策时，不追加更长的抽象提示。停止当前验收，由主会话补齐入口或决定，再创建新的窄任务。
