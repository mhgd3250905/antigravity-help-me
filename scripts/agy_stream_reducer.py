#!/usr/bin/env python3
"""Reduce Antigravity CLI stream-json to bounded supervisor events.

The reducer deliberately keeps the transport boundary boring: raw NDJSON is
read from stdin and, when requested, retained in a bounded file outside the
host model context.  stdout contains only compact semantic events.  No tool
output, text delta, or unstructured response is forwarded.
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import queue
import re
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple


DEFAULT_MAX_UPDATES = 12
DEFAULT_MAX_OUTPUT_BYTES = 2048
DEFAULT_HEARTBEAT_SECONDS = 75.0
DEFAULT_RAW_LOG_BYTES = 64 * 1024
DEFAULT_RECENT_EVENTS = 3
MAX_FIELD_CHARS = 320
MAX_TOOL_NAMES = 8
MAX_REQUIRED_TOOLS = 12
EXECUTION_PROFILES = ("REVIEW_LOCAL", "REVIEW_EXTERNAL", "CHANGE")
MAX_INPUT_LINE_BYTES = 1024 * 1024
MAX_CLI_ERROR_REASON_CHARS = 1000
# Progress is deliberately stopped with a generous reservation for the
# terminal result.  A terminal result has semantic priority over live phase
# updates: once emitted, progress cannot be retracted from stdout.
FINAL_RESERVE_BYTES = 1024
TASK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def _normalise_path(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(value)))
    except (OSError, ValueError):
        return os.path.normcase(value.strip().rstrip("\\/"))


def _is_absolute_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    # ntpath also recognises Windows-shaped paths in cross-platform fixtures.
    return os.path.isabs(value) or ntpath.isabs(value)


def _task_id_arg(value: str) -> str:
    if not TASK_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("task id must use lowercase letters, digits, or hyphens and be at most 48 characters")
    return value


def _paths_equal(left: Any, right: Any) -> bool:
    left_path = _normalise_path(left)
    right_path = _normalise_path(right)
    return bool(left_path and right_path and left_path == right_path)


def _compact_text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    cleaned: List[str] = []
    for character in value:
        codepoint = ord(character)
        if character in "\t\n\r\v\f":
            cleaned.append(" ")
        elif codepoint < 32 or codepoint == 127 or 0x80 <= codepoint <= 0x9F:
            # Do not let control-only values survive into semantic fields.
            continue
        else:
            cleaned.append(character)
    value = " ".join("".join(cleaned).split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _compact_list(value: Any, *, item_limit: int = MAX_FIELD_CHARS, max_items: int = 10) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value[:max_items]:
        text = _compact_text(item, item_limit)
        if text:
            result.append(text)
    return result


def _compact_recent(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in value[-DEFAULT_RECENT_EVENTS:]:
        if not isinstance(item, dict):
            continue
        compact: Dict[str, Any] = {}
        for key, child in item.items():
            if isinstance(child, str):
                child = _compact_text(child, 120)
            if child not in (None, ""):
                compact[_compact_text(key, 40)] = child
        if compact:
            result.append(compact)
    return result


def _safe_tool_name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("tool") or value.get("function")
        if isinstance(value, dict):
            value = value.get("name")
    text = _compact_text(value, 48)
    text = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", text).strip("_")
    return text or "tool"


def _get_nested(event: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in event:
            return event[key]
    return None


def _extract_type(event: Dict[str, Any]) -> str:
    value = _get_nested(event, "type", "event", "kind")
    return _compact_text(value, 48).lower()


def _extract_phase(event: Dict[str, Any]) -> Optional[str]:
    value = _get_nested(event, "phase", "stage", "step_type", "step", "status")
    if isinstance(value, dict):
        value = value.get("name") or value.get("type") or value.get("status")
    text = _compact_text(value, 48).lower()
    if not text:
        return None
    if any(token in text for token in ("tool", "command", "function", "exec")):
        return "tools"
    if any(token in text for token in ("think", "reason", "analysis")):
        return "thinking"
    if any(token in text for token in ("text", "response", "message")):
        return "response"
    if text in {"init", "result", "final"}:
        return text
    return text[:32]


def _extract_tool_name(event: Dict[str, Any]) -> Optional[str]:
    for key in ("tool_name", "tool", "name", "command_name"):
        value = event.get(key)
        if value:
            return _safe_tool_name(value)
    for key in ("tool_call", "tool_use", "function_call", "function", "tool_info"):
        value = event.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("tool") or value.get("function")
            if isinstance(value, dict):
                value = value.get("name")
            if value:
                return _safe_tool_name(value)
    return None


_TOOL_ID_KEYS = (
    "tool_call_id",
    "toolCallId",
    "tool_use_id",
    "toolUseId",
    "invocation_id",
    "invocationId",
    "call_id",
    "callId",
)


def _identity_value(value: Any, limit: int = 180) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if not isinstance(value, (int, str)):
        return ""
    return _compact_text(value, limit)


def _explicit_tool_call_id(event: Dict[str, Any]) -> str:
    for key in _TOOL_ID_KEYS:
        value = _identity_value(event.get(key))
        if value:
            return value
    for container_name in ("tool_call", "tool_use", "function_call", "function", "tool_info"):
        container = event.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in (*_TOOL_ID_KEYS, "id"):
            value = _identity_value(container.get(key))
            if value:
                return value
    return ""


def _extract_tool_invocation_identity(event: Dict[str, Any], tool: str) -> Optional[Tuple[str, ...]]:
    """Find a stable invocation key; absent identity deliberately disables dedupe."""
    step_value = _get_nested(event, "step_index", "stepIndex")
    step_index = _identity_value(step_value, 64)
    if step_index:
        conversation = _identity_value(
            _get_nested(event, "conversation_id", "conversationId", "session_id", "sessionId"),
            180,
        ) or "<stream>"
        return ("step", conversation, step_index, tool)

    call_id = _explicit_tool_call_id(event)
    if call_id:
        return ("call", call_id, tool)
    return None


def _extract_structured_output(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    value = event.get("structured_output")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _explicit_error_text(value: Any) -> str:
    """Return only an explicitly labelled error string, never arbitrary output."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    for key in ("error", "message", "reason", "detail"):
        child = value.get(key)
        if isinstance(child, str) and child.strip():
            return child
    return ""


def _extract_cli_error_reason(event: Dict[str, Any]) -> Optional[str]:
    """Extract a bounded diagnostic without forwarding raw CLI/tool output."""
    reason = _explicit_error_text(event.get("error"))
    if not reason:
        response = event.get("response")
        if isinstance(response, dict):
            reason = _explicit_error_text(response.get("error"))
            if not reason:
                response_status = _compact_text(response.get("status"), 64).upper()
                if response_status in {"ERROR", "FAILED", "FAILURE"}:
                    reason = _explicit_error_text(response)
        elif isinstance(response, str) and re.match(
            r"^\s*(?:(?:error|failed|failure)\b\s*[:=-]|\binvalid model selection\b)",
            response,
            flags=re.IGNORECASE,
        ):
            reason = response
    compacted = _compact_text(reason, MAX_CLI_ERROR_REASON_CHARS)
    return compacted or None


def _expanded_command_names(event: Dict[str, Any]) -> set:
    value = event.get("expanded_commands")
    if not isinstance(value, list):
        return set()
    names = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            names.add(item.strip().lower())
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.add(name.strip().lower())
    return names


class Reducer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.task_id = args.task_id
        self.expected_workspace = _normalise_path(args.workspace) if _is_absolute_path(args.workspace) else None
        self.started = time.monotonic()
        self.last_heartbeat = self.started
        self.last_phase: Optional[str] = None
        self.last_event_time = self.started
        self.update_count = 0
        self.output_bytes = 0
        self.final_seen = False
        self.final_protocol: Optional[str] = None
        self.final_outcome: Optional[str] = None
        self.final_data: Dict[str, Any] = {}
        self.conversation_id: Optional[str] = None
        self.init_seen = False
        self.init_workspace_verified = False
        self.init_workspace_reason = "init_not_seen"
        self.init_cwd: Optional[str] = None
        self.init_project: Optional[str] = None
        self.init_model: Optional[str] = None
        self.permission_mode: Optional[str] = None
        self.expanded_commands: set = set()
        self.profile_error: Optional[str] = None
        self.available_tools: Optional[set] = None
        self.missing_tools: List[str] = []
        self.capability_error: Optional[str] = None
        self.malformed_count = 0
        self.init_error: Optional[str] = "init_not_seen"
        self.tool_counts: Counter = Counter()
        self.tool_invocations: set = set()
        self.recent: Deque[Dict[str, Any]] = deque(maxlen=DEFAULT_RECENT_EVENTS)
        self.raw_lines: Deque[bytes] = deque()
        self.raw_size = 0
        self.state: Dict[str, Any] = {
            "task_id": self.task_id,
            "mode": args.task_mode,
            "execution_profile": args.execution_profile,
            "status": "running",
            "phase": None,
            "workspace": {
                "expected": self.expected_workspace or _compact_text(args.workspace, 500),
                "cwd": None,
                "verified": False,
            },
            "conversation_id": None,
            "project": None,
            "permission_mode": None,
            "capabilities": {
                "required": list(args.required_tool),
                "available": [],
                "missing": [],
                "verified": False,
            },
            "tools": {},
            "recent_events": [],
        }

    def _remember_raw(self, raw: bytes) -> None:
        if not self.args.raw_log:
            return
        # Keep complete lines where possible and bound memory/file size.
        if len(raw) > self.args.raw_log_bytes:
            raw = raw[-self.args.raw_log_bytes :]
        self.raw_lines.append(raw)
        self.raw_size += len(raw)
        while self.raw_lines and self.raw_size > self.args.raw_log_bytes:
            self.raw_size -= len(self.raw_lines.popleft())

    def _write_raw_log(self) -> None:
        if not self.args.raw_log:
            return
        path = Path(self.args.raw_log)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"".join(self.raw_lines)[-self.args.raw_log_bytes :])
        except OSError:
            # A raw-log failure must not cause raw data to be printed into the
            # host context or turn a valid result into an accepted result.
            self._add_recent("raw_log_write_failed")

    def _add_recent(self, label: str, **fields: Any) -> None:
        item: Dict[str, Any] = {"event": _compact_text(label, 48)}
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool)) and value not in ("", None):
                item[key] = _compact_text(value, 120) if isinstance(value, str) else value
        self.recent.append(item)
        self.state["recent_events"] = list(self.recent)

    def _safe_event(self, event: str, **fields: Any) -> Dict[str, Any]:
        result: Dict[str, Any] = {"event": event, "task_id": self.task_id}
        for key, value in fields.items():
            if value is None or value == "":
                continue
            if isinstance(value, str):
                result[key] = _compact_text(value, 320)
            elif isinstance(value, list):
                result[key] = _compact_recent(value) if key == "recent" else _compact_list(value, max_items=10)
            elif isinstance(value, dict):
                result[key] = value
            else:
                result[key] = value
        return result

    def _encode(self, payload: Dict[str, Any]) -> bytes:
        return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    def _output_budget(self) -> int:
        # Keep a practical minimum so even a highly compact terminal event can
        # retain its protocol, outcome, task id, and required result fields.
        return max(512, self.args.max_output_bytes)

    @staticmethod
    def _terminal_text(value: Any, limit: int, fallback: str = "truncated") -> str:
        text = _compact_text(value, limit)
        return text or fallback

    @classmethod
    def _terminal_list(cls, value: Any, item_limit: int, item_count: int) -> List[str]:
        if not isinstance(value, list):
            return ["truncated"]
        result = [
            cls._terminal_text(item, item_limit)
            for item in value[:item_count]
        ]
        return result or ["truncated"]

    def _compact_terminal(self, payload: Dict[str, Any], available: int) -> Optional[Dict[str, Any]]:
        """Return the richest deterministic terminal payload that fits.

        The initial payload is already compacted for normal output.  When the
        remaining stdout budget is smaller, optional context is removed first,
        then list cardinality and field lengths are reduced in a fixed order.
        Required semantic fields are never removed for a valid blocked or
        completed result.  The ``truncated`` marker tells the host that the
        state file/raw log should be consulted for fuller details.
        """
        protocol = payload.get("protocol")
        outcome = payload.get("outcome")
        if protocol == "blocked" and outcome == "blocked":
            # Prefer preserving more list entries before shortening their
            # values.  All candidates retain the first item of every required
            # list, even at the smallest supported budget.
            for text_limit in (320, 240, 180, 120, 80, 64, 48, 32, 24, 16, 8, 4, 1):
                for item_count in (3, 2, 1):
                    candidate = {
                        "event": "final",
                        "task_id": self.task_id,
                        "protocol": "blocked",
                        "outcome": "blocked",
                        "reason": self._terminal_text(payload.get("reason"), text_limit),
                        "missing": self._terminal_list(payload.get("missing"), text_limit, item_count),
                        "next_steps": self._terminal_list(payload.get("next_steps"), text_limit, item_count),
                        "evidence": self._terminal_list(payload.get("evidence"), text_limit, item_count),
                        "truncated": True,
                    }
                    if len(self._encode(candidate)) <= available:
                        return candidate
            return None

        if protocol == "completed" and outcome == "completed":
            for text_limit in (320, 240, 180, 120, 80, 64, 48, 32, 24, 16, 8, 4, 1):
                for item_count in (3, 2, 1):
                    candidate = {
                        "event": "final",
                        "task_id": self.task_id,
                        "protocol": "completed",
                        "outcome": "completed",
                        "summary": self._terminal_text(payload.get("summary"), text_limit),
                        "evidence": self._terminal_list(payload.get("evidence"), text_limit, item_count),
                        "truncated": True,
                    }
                    if len(self._encode(candidate)) <= available:
                        return candidate
            return None

        # Protocol errors have no schema result fields to preserve.  Retain
        # the actual diagnostic code and, when available, a bounded reason;
        # drop optional context only after those diagnostics.
        code = self._terminal_text(payload.get("code"), 120, "invalid_result")
        reason = _compact_text(payload.get("reason"), MAX_FIELD_CHARS)
        for reason_limit in (MAX_FIELD_CHARS, 240, 180, 120, 80, 64, 48, 32, 24, 16, 8, 4, 1):
            candidate = {
                "event": "final",
                "task_id": self.task_id,
                "protocol": "protocol_error",
                "code": code,
                "truncated": True,
            }
            if reason:
                candidate["reason"] = self._terminal_text(reason, reason_limit)
            if len(self._encode(candidate)) <= available:
                return candidate
        candidate = {
            "event": "final",
            "task_id": self.task_id,
            "protocol": "protocol_error",
            "code": "invalid_result",
            "truncated": True,
        }
        if len(self._encode(candidate)) <= available:
            return candidate
        return None

    def _emit_terminal(self, payload: Dict[str, Any]) -> bool:
        budget = self._output_budget()
        available = budget - self.output_bytes
        encoded = self._encode(payload)
        if len(encoded) > available:
            compacted = self._compact_terminal(payload, available)
            if compacted is None:
                # The progress reservation guarantees this is unreachable for
                # supported budgets.  Never rewrite a valid terminal semantic
                # into output_budget_exceeded; preserve the original protocol
                # in the state file and leave stdout untouched if impossible.
                return False
            payload = compacted
            encoded = self._encode(payload)
        if len(encoded) > available:
            return False
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        self.output_bytes += len(encoded)
        self.update_count += 1
        return True

    def _emit(self, payload: Dict[str, Any], *, force: bool = False) -> bool:
        """Print a compact event, respecting count and byte budgets."""
        is_final = payload.get("event") == "final"
        if is_final:
            return self._emit_terminal(payload)
        # Keep one update slot for the terminal event.  The byte reservation
        # below is independent of the count limit and has higher priority.
        if not force and self.update_count >= max(1, self.args.max_updates) - 1:
            return False

        encoded = self._encode(payload)
        budget = self._output_budget()
        if not force and self.output_bytes + len(encoded) > budget - FINAL_RESERVE_BYTES:
            return False
        if self.output_bytes + len(encoded) > budget:
            return False
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        self.output_bytes += len(encoded)
        self.update_count += 1
        return True

    def _update_state(self) -> None:
        self.state["phase"] = self.last_phase
        self.state["conversation_id"] = self.conversation_id
        self.state["project"] = self.init_project
        self.state["permission_mode"] = self.permission_mode
        self.state["execution_profile"] = self.args.execution_profile
        self.state["expanded_commands"] = sorted(self.expanded_commands)
        self.state["tools"] = dict(self.tool_counts.most_common(MAX_TOOL_NAMES))
        required_tools = list(self.args.required_tool)
        available_required = (
            sorted(set(required_tools).intersection(self.available_tools))
            if self.available_tools is not None
            else []
        )
        self.state["capabilities"] = {
            "required": required_tools,
            "available": available_required,
            "missing": list(self.missing_tools),
            "verified": bool(not required_tools or (self.available_tools is not None and not self.missing_tools)),
        }
        self.state["workspace"] = {
            "expected": self.expected_workspace or _compact_text(self.args.workspace, 500),
            "cwd": self.init_cwd,
            "verified": self.init_workspace_verified,
            "reason": self.init_workspace_reason,
        }
        if self.final_data:
            self.state["final"] = {
                "protocol": self.final_protocol,
                **self.final_data,
            }
        try:
            self.state["status"] = self.final_protocol or "running"
            if self.args.state:
                path = Path(self.args.state)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + ".tmp")
                temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, path)
        except OSError:
            self._add_recent("state_write_failed")

    def _emit_init(self, event: Dict[str, Any]) -> None:
        if self.init_seen:
            # init binds the process exactly once.  A later event must not be
            # able to repair an earlier workspace/profile/capability failure,
            # nor silently replace the identity already recorded in state.
            self.init_error = "duplicate_init"
            self._add_recent("duplicate_init")
            self._emit(self._safe_event("warning", code="duplicate_init"))
            return
        self.init_seen = True
        self.init_cwd = _normalise_path(event.get("cwd"))
        self.conversation_id = _compact_text(
            _get_nested(event, "conversation_id", "conversationId", "session_id", "sessionId"), 180
        ) or None
        self.init_model = _compact_text(_get_nested(event, "model", "model_name"), 120) or None
        self.permission_mode = _compact_text(_get_nested(event, "permission_mode", "permissionMode"), 80) or None
        self.init_project = _compact_text(_get_nested(event, "project", "project_id", "projectId"), 180) or None
        self.expanded_commands = _expanded_command_names(event)

        expected_mode = "CHANGE" if self.args.execution_profile == "CHANGE" else "REVIEW"
        if self.args.task_mode != expected_mode:
            self.profile_error = "mode_profile_mismatch"
        elif self.args.execution_profile == "REVIEW_LOCAL" and "plan" not in self.expanded_commands:
            self.profile_error = "plan_not_active"
        elif self.args.execution_profile != "REVIEW_LOCAL" and "plan" in self.expanded_commands:
            self.profile_error = "unexpected_plan_mode"
        if self.args.expected_conversation and self.conversation_id != self.args.expected_conversation:
            self.profile_error = "conversation_mismatch"
        if self.args.expected_project and self.init_project != self.args.expected_project:
            self.profile_error = "project_mismatch"
        if self.args.expected_permission_mode and self.permission_mode != self.args.expected_permission_mode:
            self.profile_error = "permission_mode_mismatch"

        raw_tools = event.get("tools")
        if isinstance(raw_tools, list):
            self.available_tools = {
                item.strip() for item in raw_tools if isinstance(item, str) and item.strip()
            }
        else:
            self.available_tools = None
        required_tools = list(self.args.required_tool)
        if required_tools:
            if self.available_tools is None:
                self.missing_tools = required_tools[:MAX_REQUIRED_TOOLS]
                self.capability_error = "capability_probe_unavailable"
            else:
                self.missing_tools = [tool for tool in required_tools if tool not in self.available_tools]
                if self.missing_tools:
                    self.capability_error = "capability_missing"

        # agy 1.1.22 documents and emits cwd, but does not document an
        # added-dir field in init.  Do not let arbitrary nested metadata stand
        # in for the process binding proof.
        self.init_workspace_verified = bool(
            self.expected_workspace and self.init_cwd == self.expected_workspace
        )
        if self.init_workspace_verified:
            self.init_workspace_reason = "cwd_matches"
            self.init_error = None
        elif not self.expected_workspace:
            self.init_workspace_reason = "expected_workspace_not_absolute"
            self.init_error = "workspace_not_absolute"
        elif not self.init_cwd:
            self.init_workspace_reason = "init_cwd_missing"
            self.init_error = "workspace_binding_unverified"
        else:
            self.init_workspace_reason = "cwd_does_not_match"
            self.init_error = "workspace_binding_mismatch"

        if self.profile_error and self.init_error is None:
            self.init_error = self.profile_error
        elif self.capability_error and self.init_error is None:
            self.init_error = self.capability_error

        self.last_phase = "init"
        self._add_recent("init", workspace="verified" if self.init_workspace_verified else "unverified")
        self._emit(
            self._safe_event(
                "init",
                workspace="verified" if self.init_workspace_verified else "unverified",
                cwd=self.init_cwd,
                model=self.init_model,
                permission_mode=self.permission_mode,
                conversation_id=self.conversation_id,
                project=self.init_project,
                execution_profile=self.args.execution_profile,
                plan_active="plan" in self.expanded_commands,
                required_tools=required_tools,
                available_tools=(
                    sorted(set(required_tools).intersection(self.available_tools))
                    if self.available_tools is not None
                    else []
                ),
                missing_tools=self.missing_tools,
            )
        )
        if self.capability_error:
            self._add_recent("capability_error", code=self.capability_error)
            self._emit(self._safe_event("warning", code=self.capability_error, missing_tools=self.missing_tools))
        if self.profile_error:
            self._add_recent("profile_error", code=self.profile_error)
            self._emit(self._safe_event("warning", code=self.profile_error))

    def _maybe_heartbeat(self, now: float) -> None:
        if self.args.heartbeat_seconds < 0:
            return
        if now - self.last_heartbeat < self.args.heartbeat_seconds:
            return
        self.last_heartbeat = now
        tools = dict(self.tool_counts.most_common(MAX_TOOL_NAMES))
        self._add_recent("heartbeat", phase=self.last_phase or "unknown")
        self._emit(
            self._safe_event(
                "heartbeat",
                phase=self.last_phase or "unknown",
                elapsed_seconds=round(now - self.started, 1),
                tools=tools,
            )
        )

    def _handle_step(self, event: Dict[str, Any], now: float) -> None:
        phase = _extract_phase(event)
        tool = _extract_tool_name(event)
        new_invocation = False
        stable_identity = False
        if tool:
            identity_event = event
            if self.conversation_id and not _identity_value(
                _get_nested(event, "conversation_id", "conversationId", "session_id", "sessionId"),
                180,
            ):
                # Some envelopes put the conversation id only on init.  Use
                # that process binding for a step-index key, but never invent
                # an identity when both the binding and step/call id are absent.
                identity_event = dict(event)
                identity_event["conversation_id"] = self.conversation_id
            identity = _extract_tool_invocation_identity(identity_event, tool)
            if identity is None:
                # Compatibility envelopes without a step/call identity are
                # treated as separate observations.  Merging them by tool
                # name would undercount distinct real calls.
                new_invocation = True
            elif identity not in self.tool_invocations:
                self.tool_invocations.add(identity)
                new_invocation = True
                stable_identity = True
            if new_invocation:
                self.tool_counts[tool] += 1

        phase_changed = bool(phase and phase != self.last_phase)
        if phase_changed:
            self.last_phase = phase
            self._add_recent("phase", phase=phase, tool=tool or "")
            self._emit(self._safe_event("phase", phase=phase, tool=tool, count=self.tool_counts.get(tool, 0) if tool else None))
        elif new_invocation and stable_identity and phase == "tools":
            # A new invocation may follow another tool invocation without an
            # intervening non-tool phase.  Report that count once, while
            # suppressing ACTIVE/DONE/ERROR updates for the same identity.
            self._add_recent("phase", phase=phase, tool=tool or "")
            self._emit(self._safe_event("phase", phase=phase, tool=tool, count=self.tool_counts.get(tool, 0)))
        self._maybe_heartbeat(now)

    def _handle_warning(self, event: Dict[str, Any]) -> None:
        status = _compact_text(_get_nested(event, "status", "level", "severity", "error"), 64).lower()
        code = "agy_warning"
        if "block" in status:
            code = "agy_blocked_signal"
        elif "error" in status or event.get("error"):
            code = "agy_error_signal"
        self._add_recent("warning", code=code)
        self._emit(self._safe_event("warning", code=code))

    def _validate_result(self, event: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        status = _compact_text(event.get("status"), 64).upper()
        structured = _extract_structured_output(event)
        response = event.get("response")
        if isinstance(response, str) and response.strip().upper() == "BLOCKED" and structured is None:
            return "protocol_error", {"code": "bare_blocked"}
        if status and status not in {"SUCCESS", "BLOCKED"}:
            data: Dict[str, Any] = {"code": "cli_status", "status": status}
            reason = _extract_cli_error_reason(event)
            if reason:
                data["reason"] = reason
            return "protocol_error", data
        if structured is None:
            return "protocol_error", {"code": "missing_structured_output"}

        required = ("task_id", "outcome", "summary", "reason", "missing", "next_steps", "evidence")
        missing_fields = [key for key in required if key not in structured]
        if missing_fields:
            return "protocol_error", {"code": "schema_missing_fields", "fields": missing_fields}
        extra_fields = sorted(set(structured) - set(required))
        if extra_fields:
            return "protocol_error", {"code": "schema_extra_fields", "fields": extra_fields[:8]}
        if structured.get("task_id") != self.task_id:
            return "protocol_error", {"code": "task_id_mismatch"}
        if not isinstance(structured.get("task_id"), str) or not TASK_ID_PATTERN.fullmatch(structured["task_id"]):
            return "protocol_error", {"code": "invalid_task_id"}
        outcome = structured.get("outcome")
        if outcome not in {"completed", "blocked"}:
            return "protocol_error", {"code": "invalid_outcome"}
        summary = _compact_text(structured.get("summary"), 2000)
        if not isinstance(structured.get("summary"), str) or not 1 <= len(structured["summary"]) <= 2000 or not summary:
            return "protocol_error", {"code": "empty_summary"}
        if not isinstance(structured.get("reason"), str) or len(structured["reason"]) > 2000:
            return "protocol_error", {"code": "invalid_reason"}
        reason = _compact_text(structured.get("reason"), 2000)
        list_limits = {"missing": 10, "next_steps": 10, "evidence": 20}
        cleaned_lists: Dict[str, List[str]] = {}
        for field, max_items in list_limits.items():
            if not isinstance(structured.get(field), list):
                return "protocol_error", {"code": "invalid_" + field}
            if len(structured[field]) > max_items:
                return "protocol_error", {"code": "too_many_" + field}
            if any(not isinstance(item, str) or len(item) > 500 for item in structured[field]):
                return "protocol_error", {"code": "invalid_" + field + "_item"}
            cleaned_lists[field] = []
            for item in structured[field]:
                cleaned_item = _compact_text(item, 500)
                if not cleaned_item:
                    return "protocol_error", {"code": "empty_" + field + "_item"}
                cleaned_lists[field].append(cleaned_item)
        if outcome == "blocked":
            if not reason:
                return "protocol_error", {"code": "blocked_reason_empty"}
            if not cleaned_lists["missing"]:
                return "protocol_error", {"code": "blocked_missing_empty"}
            if not cleaned_lists["next_steps"]:
                return "protocol_error", {"code": "blocked_next_steps_empty"}
            if not cleaned_lists["evidence"]:
                return "protocol_error", {"code": "blocked_evidence_empty"}
        elif not cleaned_lists["evidence"]:
            return "protocol_error", {"code": "completed_evidence_empty"}
        return "completed", {
            "outcome": outcome,
            # Keep the validated result at schema bounds for state.json.  The
            # stdout terminal is compacted separately according to the bytes
            # still available, so state remains the fuller diagnostic source.
            "summary": summary,
            "reason": reason,
            "missing": cleaned_lists["missing"],
            "next_steps": cleaned_lists["next_steps"],
            "evidence": cleaned_lists["evidence"],
        }

    @staticmethod
    def _final_list(value: Any) -> List[str]:
        return _compact_list(value, item_limit=120, max_items=3)

    def _handle_result(self, event: Dict[str, Any]) -> None:
        duplicate = self.final_seen
        self.final_seen = True
        if not self.conversation_id:
            self.conversation_id = _compact_text(
                _get_nested(event, "conversation_id", "conversationId", "session_id", "sessionId"), 180
            ) or None
        if duplicate:
            protocol, data = "protocol_error", {"code": "duplicate_final"}
        elif self.malformed_count:
            # A valid-looking final must not rescue a stream that already
            # violated the NDJSON transport contract.
            protocol, data = "protocol_error", {"code": "malformed_stream"}
        else:
            protocol, data = self._validate_result(event)
        # A missing or mismatched workspace must never become an accepted task
        # merely because the model returned a valid-looking object.
        if protocol == "completed" and self.init_error:
            protocol = "protocol_error"
            data = {"code": self.init_error}
        self.final_protocol = "blocked" if protocol == "completed" and data.get("outcome") == "blocked" else protocol
        self.final_outcome = data.get("outcome") if protocol == "completed" else None
        self.final_data = data
        if self.final_protocol == "blocked":
            self.state["status"] = "blocked"
            self._add_recent("blocked", reason=data.get("reason", ""))
            final_payload = self._safe_event(
                "final",
                protocol="blocked",
                outcome="blocked",
                reason=_compact_text(data.get("reason"), 180),
                missing=self._final_list(data.get("missing")),
                next_steps=self._final_list(data.get("next_steps")),
                evidence=self._final_list(data.get("evidence")),
                conversation_id=self.conversation_id,
            )
            if any(final_payload.get(field) != data.get(field) for field in ("reason", "missing", "next_steps", "evidence")):
                final_payload["truncated"] = True
            self._emit(final_payload, force=True)
        elif self.final_protocol == "completed":
            self.state["status"] = "completed"
            self._add_recent("completed", outcome=data.get("outcome", "completed"))
            final_payload = self._safe_event(
                "final",
                protocol="completed",
                outcome=data.get("outcome"),
                summary=_compact_text(data.get("summary"), 240),
                evidence=self._final_list(data.get("evidence")),
                conversation_id=self.conversation_id,
            )
            if any(final_payload.get(field) != data.get(field) for field in ("summary", "evidence")):
                final_payload["truncated"] = True
            self._emit(final_payload, force=True)
        else:
            self.state["status"] = "protocol_error"
            self._add_recent("protocol_error", code=data.get("code", "invalid_result"))
            final_payload = self._safe_event(
                "final",
                protocol="protocol_error",
                code=data.get("code", "invalid_result"),
                reason=data.get("reason"),
                fields=data.get("fields"),
                recent=list(self.recent),
                conversation_id=self.conversation_id,
            )
            if data.get("reason") and final_payload.get("reason") != data.get("reason"):
                final_payload["truncated"] = True
            self._emit(final_payload, force=True)
        self._update_state()

    def process(self, event: Dict[str, Any]) -> None:
        now = time.monotonic()
        self.last_event_time = now
        event_type = _extract_type(event)
        if event_type == "init":
            payload = event.get("init") if isinstance(event.get("init"), dict) else event
            payload = dict(payload)
            if "conversation_id" not in payload:
                payload["conversation_id"] = _get_nested(event, "conversation_id", "conversationId")
            self._emit_init(payload)
            self._update_state()
            return
        if event_type in {"result", "final"}:
            payload = event.get(event_type)
            self._handle_result(payload if isinstance(payload, dict) else event)
            return
        if event_type in {"step_update", "step", "progress", "tool_call", "tool_use"}:
            payload = event.get(event_type)
            if isinstance(payload, dict):
                payload = dict(payload)
                if not any(key in payload for key in ("conversation_id", "conversationId", "session_id", "sessionId")):
                    conversation_id = _get_nested(event, "conversation_id", "conversationId", "session_id", "sessionId")
                    if conversation_id is not None:
                        payload["conversation_id"] = conversation_id
            self._handle_step(payload if isinstance(payload, dict) else event, now)
            self._update_state()
            return
        status = _compact_text(_get_nested(event, "status", "level", "severity"), 64).lower()
        if event.get("error") or status in {"warning", "warn", "error", "blocked", "failed"}:
            self._handle_warning(event)
            self._update_state()
            return
        # Unknown events are intentionally ignored.  They may contain text or
        # tool payloads and must not be forwarded into the host context.
        self._maybe_heartbeat(now)

    def finish(self) -> int:
        if not self.final_seen:
            self.final_protocol = "protocol_error"
            self.state["status"] = "protocol_error"
            self._add_recent("protocol_error", code="missing_final")
            self._emit(
                self._safe_event(
                    "final",
                    protocol="protocol_error",
                    code="missing_final" if not self.malformed_count else "malformed_stream",
                    recent=list(self.recent),
                    conversation_id=self.conversation_id,
                ),
                force=True,
            )
            self._update_state()
        self._write_raw_log()
        if self.final_protocol in {"completed", "blocked"}:
            return 0
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", type=_task_id_arg, required=True)
    parser.add_argument("--task-mode", choices=("REVIEW", "CHANGE"), required=True)
    parser.add_argument("--execution-profile", choices=EXECUTION_PROFILES, required=True)
    parser.add_argument("--expected-conversation", help="Conversation id required for an explicit resume")
    parser.add_argument("--expected-project", help="Project id required when the launch declares one")
    parser.add_argument("--expected-permission-mode", help="Exact init permission_mode required by the launch policy")
    parser.add_argument(
        "--required-tool",
        action="append",
        default=[],
        help="Exact Agy tool name required by the task; repeat for multiple tools",
    )
    parser.add_argument("--workspace", required=True, help="Expected absolute workspace path")
    parser.add_argument("--state", help="Path for compact latest-state JSON (outside model context)")
    parser.add_argument("--raw-log", help="Path for bounded raw NDJSON retention (outside model context)")
    parser.add_argument("--raw-log-bytes", type=int, default=DEFAULT_RAW_LOG_BYTES)
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--max-updates", type=int, default=DEFAULT_MAX_UPDATES)
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    return parser


def _read_stdin(input_queue: "queue.Queue[Optional[bytes]]") -> None:
    pending = bytearray()
    discard_until_newline = False

    def enqueue_line(raw: bytes) -> None:
        # Check the complete NDJSON line, including its newline, before it can
        # enter the processing queue.  A line may cross several os.read chunks;
        # checking only ``pending`` before finding a later newline lets an
        # oversized complete JSON event bypass the input bound.
        if len(raw) > MAX_INPUT_LINE_BYTES:
            input_queue.put(b"\n")
        else:
            input_queue.put(raw)

    try:
        descriptor = sys.stdin.fileno()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            if discard_until_newline:
                newline = chunk.find(b"\n")
                if newline < 0:
                    continue
                discard_until_newline = False
                chunk = chunk[newline + 1 :]
                if not chunk:
                    continue
            pending.extend(chunk)
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    break
                raw = bytes(pending[: newline + 1])
                del pending[: newline + 1]
                enqueue_line(raw)
            if len(pending) > MAX_INPUT_LINE_BYTES:
                # Emit one deliberately malformed marker without retaining or
                # forwarding the oversized payload.  Discard the remainder of
                # the same logical line if its newline arrives in a later
                # read; it must not be treated as a fresh JSON event.
                input_queue.put(b"\n")
                pending.clear()
                discard_until_newline = True
        if pending:
            enqueue_line(bytes(pending))
    except (OSError, ValueError):
        # The final protocol event will explain that the stream ended without
        # a valid result; never echo an OS error or any partial raw payload.
        pass
    finally:
        input_queue.put(None)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.max_updates < 1:
        args.max_updates = 1
    if args.max_output_bytes < 512:
        args.max_output_bytes = 512
    if args.raw_log_bytes < 1024:
        args.raw_log_bytes = 1024
    reducer = Reducer(args)
    # Bound the hand-off as well as the retained raw ring.  Back-pressure is
    # preferable to allowing a noisy producer to grow host memory without
    # limit.
    input_queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=256)
    reader = threading.Thread(target=_read_stdin, args=(input_queue,), daemon=True)
    reader.start()
    try:
        while True:
            timeout: Optional[float] = None
            if args.heartbeat_seconds >= 0 and not reducer.final_seen:
                remaining = args.heartbeat_seconds - (time.monotonic() - reducer.last_heartbeat)
                # A small floor avoids a busy loop when tests intentionally set
                # heartbeat=0 while still allowing a real idle heartbeat.
                timeout = max(0.05, remaining)
            try:
                raw = input_queue.get(timeout=timeout)
            except queue.Empty:
                if not reducer.final_seen:
                    reducer._maybe_heartbeat(time.monotonic())
                    reducer._update_state()
                continue
            if raw is None:
                break
            reducer._remember_raw(raw)
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                reducer.malformed_count += 1
                reducer._add_recent("malformed_ndjson")
                continue
            if isinstance(value, dict):
                reducer.process(value)
                reducer._update_state()
                if reducer.final_seen:
                    # result/final is terminal.  Do not depend on the producer
                    # closing stdout promptly after its terminal event.
                    break
            else:
                reducer.malformed_count += 1
                reducer._add_recent("non_object_event")
    except KeyboardInterrupt:
        reducer._add_recent("interrupted")
    return reducer.finish()


if __name__ == "__main__":
    raise SystemExit(main())
