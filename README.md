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

**Prerequisite** — `agy` is installed and signed in (`agy --version` works). The skill does not install or sign in for you.
