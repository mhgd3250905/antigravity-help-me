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
FINAL_RESERVE_BYTES = 320
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
    value = " ".join(value.replace("\x00", "").split())
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
    for key in ("tool_call", "tool_use", "function_call", "function"):
        value = event.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("tool") or value.get("function")
            if isinstance(value, dict):
                value = value.get("name")
            if value:
                return _safe_tool_name(value)
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

    def _emit(self, payload: Dict[str, Any], *, force: bool = False) -> bool:
        """Print a compact event, respecting count and byte budgets."""
        is_final = payload.get("event") == "final"
        reserve = 0 if is_final else 1  # Keep room for the terminal event.
        if not force and self.update_count >= max(1, self.args.max_updates) - reserve:
            return False

        encoded = self._encode(payload)
        budget = max(512, self.args.max_output_bytes)
        if not is_final and not force and self.output_bytes + len(encoded) > budget - FINAL_RESERVE_BYTES:
            return False
        if self.output_bytes + len(encoded) > budget:
            if not is_final and not force:
                return False
            # Final output is still useful when prior optional events consumed
            # the budget; reduce it to a minimal, non-sensitive record.
            minimal = self._safe_event(
                "final",
                protocol=payload.get("protocol", "protocol_error"),
                outcome=payload.get("outcome"),
                code=payload.get("code", "output_budget_exceeded"),
            )
            encoded = self._encode(minimal)
            if len(encoded) > budget:
                minimal = {"event": "final", "task_id": self.task_id, "protocol": "protocol_error", "code": "output_budget_exceeded"}
                encoded = self._encode(minimal)
            # Do not exceed the advertised output budget.  Non-final events
            # reserve room for this compact terminal record.
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
        if phase and phase != self.last_phase:
            self.last_phase = phase
            tool = _extract_tool_name(event)
            if tool:
                self.tool_counts[tool] += 1
            self._add_recent("phase", phase=phase, tool=tool or "")
            self._emit(self._safe_event("phase", phase=phase, tool=tool, count=self.tool_counts.get(tool, 0) if tool else None))
        else:
            tool = _extract_tool_name(event)
            if tool:
                self.tool_counts[tool] += 1
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
            return "protocol_error", {"code": "cli_status", "status": status}
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
        if not isinstance(structured.get("summary"), str) or not 1 <= len(structured["summary"]) <= 2000 or not structured["summary"].strip():
            return "protocol_error", {"code": "empty_summary"}
        if not isinstance(structured.get("reason"), str) or len(structured["reason"]) > 2000:
            return "protocol_error", {"code": "invalid_reason"}
        list_limits = {"missing": 10, "next_steps": 10, "evidence": 20}
        for field, max_items in list_limits.items():
            if not isinstance(structured.get(field), list):
                return "protocol_error", {"code": "invalid_" + field}
            if len(structured[field]) > max_items:
                return "protocol_error", {"code": "too_many_" + field}
            if any(not isinstance(item, str) or len(item) > 500 for item in structured[field]):
                return "protocol_error", {"code": "invalid_" + field + "_item"}
        if outcome == "blocked":
            if not structured["reason"].strip():
                return "protocol_error", {"code": "blocked_reason_empty"}
            if not structured["missing"]:
                return "protocol_error", {"code": "blocked_missing_empty"}
            if not structured["next_steps"]:
                return "protocol_error", {"code": "blocked_next_steps_empty"}
            if not structured["evidence"]:
                return "protocol_error", {"code": "blocked_evidence_empty"}
        elif not structured["evidence"]:
            return "protocol_error", {"code": "completed_evidence_empty"}
        return "completed", {
            "outcome": outcome,
            "summary": _compact_text(structured.get("summary")),
            "reason": _compact_text(structured.get("reason")),
            "missing": _compact_list(structured.get("missing")),
            "next_steps": _compact_list(structured.get("next_steps")),
            "evidence": _compact_list(structured.get("evidence")),
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
            self._emit(
                self._safe_event(
                    "final",
                    protocol="blocked",
                    outcome="blocked",
                    reason=_compact_text(data.get("reason"), 180),
                    missing=self._final_list(data.get("missing")),
                    next_steps=self._final_list(data.get("next_steps")),
                    evidence=self._final_list(data.get("evidence")),
                    conversation_id=self.conversation_id,
                ),
                force=True,
            )
        elif self.final_protocol == "completed":
            self.state["status"] = "completed"
            self._add_recent("completed", outcome=data.get("outcome", "completed"))
            self._emit(
                self._safe_event(
                    "final",
                    protocol="completed",
                    outcome=data.get("outcome"),
                    summary=_compact_text(data.get("summary"), 240),
                    evidence=self._final_list(data.get("evidence")),
                    conversation_id=self.conversation_id,
                ),
                force=True,
            )
        else:
            self.state["status"] = "protocol_error"
            self._add_recent("protocol_error", code=data.get("code", "invalid_result"))
            self._emit(
                self._safe_event(
                    "final",
                    protocol="protocol_error",
                    code=data.get("code", "invalid_result"),
                    fields=data.get("fields"),
                    recent=list(self.recent),
                    conversation_id=self.conversation_id,
                ),
                force=True,
            )
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
    try:
        descriptor = sys.stdin.fileno()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            pending.extend(chunk)
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    break
                raw = bytes(pending[: newline + 1])
                del pending[: newline + 1]
                input_queue.put(raw)
            if len(pending) > MAX_INPUT_LINE_BYTES:
                # Emit one deliberately malformed marker without retaining or
                # forwarding the oversized payload.
                input_queue.put(b"\n")
                pending.clear()
        if pending:
            input_queue.put(bytes(pending))
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
