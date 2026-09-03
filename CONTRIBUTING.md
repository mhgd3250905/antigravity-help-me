# Contributing to Antigravity Help Me

Thank you for your interest in improving `antigravity-help-me`! We welcome contributions that uphold the core principles of simplicity, reliability, and security.

---

## Core Design Principles

Before submitting changes, please ensure your contribution aligns with the project's non-negotiable architectural decisions:

1. **Standalone Architecture**:
   - The delegation and supervision protocol remains self-contained in the skill and its focused references.
   - Execution uses the native Antigravity CLI (`agy`) from the host Agent's built-in terminal.

2. **File-Based Task Contract (TASK.md)**:
   - Tasks are articulated in `.antigravity-help-me/tasks/<task-id>/TASK.md`.
   - The CLI prompt passed in `argv` is fixed and minimal to eliminate shell quoting hazards, command line length limits, and prompt injection vulnerabilities.

3. **Explicit Model Selection & Fail-Fast**:
   - Defaults to `gemini-3.8-flash-high` with optional `--model` override.
   - No silent fallbacks or implicit model switching. If the target model or CLI is unavailable, fail immediately with clear diagnostic logs.

4. **Clear Separation of Duties**:
   - The **Host Agent** acts as the supervisor (shaping tasks, judging architecture/priority, supervising execution, and performing final verification).
   - **Agy / Gemini** acts as the dedicated execution workstation (reading TASK.md, making scoped edits, running tests, returning evidence).

---

## How to Contribute

### 1. Reporting Issues
- Use the [Bug Report](.github/ISSUE_TEMPLATE/bug_report.yml) template.
- Include your operating system, host agent (Codex, Claude Code, etc.), `agy --version`, and exact error logs.
- For security vulnerabilities, **do not** file a public issue; see [SECURITY.md](SECURITY.md).

### 2. Suggesting Features
- Use the [Feature Request](.github/ISSUE_TEMPLATE/feature_request.yml) template.
- Ensure the proposed feature preserves the standalone skill contract and native Agy CLI approach.

### 3. Submitting Pull Requests
1. Fork the repository and create your branch from `main`.
2. Keep changes focused and adhere to existing Markdown formatting and YAML standards.
3. Validate your changes locally (see below).
4. Ensure no absolute personal paths, secrets, tokens, or temporary files are included.
5. Submit a pull request describing the rationale and testing performed.

---

## Local Validation

Before submitting a PR, validate the skill definition using the standard skill validator:

```bash
# On Linux/macOS
python <path-to-skill-creator>/scripts/quick_validate.py .

# On Windows PowerShell (ensuring UTF-8 parsing)
$env:PYTHONUTF8=1; python <path-to-skill-creator>/scripts/quick_validate.py .; Remove-Item Env:\PYTHONUTF8
```

Verify that:
- [SKILL.md](SKILL.md) passes all frontmatter formatting and length checks.
- All relative links to `references/*.md` are valid and resolve correctly.
- `agents/openai.yaml` remains valid YAML.
- No personal file paths or system-specific artifacts are committed.
