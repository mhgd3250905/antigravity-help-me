# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.3.x   | :white_check_mark: |
| < 1.3   | :x:                |

---

## Reporting a Vulnerability

We take the security of `antigravity-help-me` seriously. If you discover a security vulnerability, please do **NOT** open a public issue.

Instead, please report it via one of the following methods:
- **GitHub Private Vulnerability Reporting**: Submit a private advisory report directly via [GitHub Security Advisories](https://github.com/mhgd3250905/antigravity-help-me/security/advisories/new).
- **Direct Maintainer Contact**: Contact the repository maintainer privately via GitHub profile contact options.

Please include:
1. A detailed description of the vulnerability.
2. Steps or proof-of-concept demonstrating the issue.
3. The potential impact on the host system or workspace.
4. Any suggested mitigations or patches.

We will acknowledge receipt of your report promptly and coordinate disclosure responsibly.

---

## Security Model & Boundaries

`antigravity-help-me` coordinates tasks between a **Host Agent** and the native **Antigravity CLI (`agy`)**. Understanding the trust boundary is essential for secure operation:

1. **`--dangerously-skip-permissions` is Not a Security Sandbox**:
   - The `--dangerously-skip-permissions` flag allows headless `agy` to invoke tools without interactive confirmation prompts.
   - It does **not** restrict filesystem or network access.
   - Security boundaries and user authorizations must be actively enforced by the **Host Agent** before dispatching commands.

2. **Handling Untrusted Inputs**:
   - Third-party pull requests, cloned external repositories, untrusted web content, issue text, logs, and evidence attachments must be treated strictly as **data**, not instructions.
   - When executing tasks involving untrusted content, the host agent should run the workflow in an isolated environment (e.g., disposable containers, virtual machines, or isolated worktrees) without access to sensitive credentials or production networks.
   - See [references/permissions.md](references/permissions.md) for detailed guidelines.

3. **Prompt Injection Mitigation**:
   - By using a fixed, short prompt in the CLI invocation and isolating task requirements in a local `TASK.md` contract file, the skill prevents arbitrary command line injection and unescaped variable expansion in host shells (such as PowerShell or Bash).

4. **No Privilege Elevation or Credential Modification**:
   - `antigravity-help-me` never attempts to alter global system PATH, edit shell profiles, tamper with OAuth tokens in `~/.gemini`, or bypass host sandbox constraints.
