"""Deterministic contract tests for the stream-json transport reducer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
REDUCER = ROOT / "scripts" / "agy_stream_reducer.py"


def _event_lines(events: Iterable[Dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n").encode("utf-8")


def _init(workspace: Path, task_id: str = "fixture-task", tools: Optional[List[str]] = None) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "type": "init",
        "cwd": str(workspace),
        "model": "gemini-3.7-flash-high",
        "permission_mode": "request-review",
        "conversation_id": "fixture-conversation",
        "project_id": "fixture-project",
    }
    if tools is not None:
        event["tools"] = tools
    return event


def _completed(task_id: str = "fixture-task") -> Dict[str, Any]:
    return {
        "type": "result",
        "status": "SUCCESS",
        "response": "SENSITIVE_RAW_RESPONSE_SHOULD_NOT_APPEAR",
        "structured_output": {
            "task_id": task_id,
            "outcome": "completed",
            "summary": "The fixture task completed.",
            "reason": "",
            "missing": [],
            "next_steps": [],
            "evidence": ["tests/fixture-result.txt"],
        },
    }


def _run(
    events: Iterable[Dict[str, Any]],
    workspace: Path,
    *,
    task_id: str = "fixture-task",
    heartbeat: float = 75,
    max_updates: int = 12,
    max_output_bytes: int = 2048,
    raw_log_bytes: int = 65536,
    required_tools: Iterable[str] = (),
    raw_prefix: bytes = b"",
    execution_profile: str = "REVIEW_EXTERNAL",
    expected_conversation: Optional[str] = None,
    expected_project: Optional[str] = None,
    expected_permission_mode: Optional[str] = None,
) -> Tuple[subprocess.CompletedProcess[bytes], List[Dict[str, Any]], Path, Path]:
    with tempfile.TemporaryDirectory(prefix="agy-reducer-test-") as temp:
        temp_path = Path(temp)
        state = temp_path / "state.json"
        raw_log = temp_path / "stream.ndjson"
        command = [
            sys.executable,
            "-B",
            str(REDUCER),
            "--task-id",
            task_id,
            "--workspace",
            str(workspace),
            "--task-mode",
            "REVIEW",
            "--execution-profile",
            execution_profile,
            "--state",
            str(state),
            "--raw-log",
            str(raw_log),
            "--heartbeat-seconds",
            str(heartbeat),
            "--max-updates",
            str(max_updates),
            "--max-output-bytes",
            str(max_output_bytes),
            "--raw-log-bytes",
            str(raw_log_bytes),
        ]
        for tool in required_tools:
            command.extend(["--required-tool", tool])
        if expected_conversation:
            command.extend(["--expected-conversation", expected_conversation])
        if expected_project:
            command.extend(["--expected-project", expected_project])
        if expected_permission_mode:
            command.extend(["--expected-permission-mode", expected_permission_mode])
        completed = subprocess.run(
            command,
            input=raw_prefix + _event_lines(events),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            check=False,
        )
        # Copy the two bounded artifacts before TemporaryDirectory cleanup.
        output_events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        state_fd, state_name = tempfile.mkstemp(prefix="agy-state-copy-", suffix=".json")
        raw_fd, raw_name = tempfile.mkstemp(prefix="agy-raw-copy-", suffix=".ndjson")
        os.close(state_fd)
        os.close(raw_fd)
        saved_state = Path(state_name)
        saved_raw = Path(raw_name)
        if state.exists():
            saved_state.write_bytes(state.read_bytes())
        if raw_log.exists():
            saved_raw.write_bytes(raw_log.read_bytes())
    return completed, output_events, saved_state, saved_raw


class ReducerContractTests(unittest.TestCase):
    def test_init_phase_aggregation_and_heartbeat_are_compact(self) -> None:
        workspace = ROOT.resolve()
        events: List[Dict[str, Any]] = [_init(workspace)]
        events.extend(
            {
                "type": "step_update",
                "step_type": "tool_call",
                "tool_name": "rg",
                "tool_output": "SECRET_TOOL_OUTPUT_DO_NOT_FORWARD",
            }
            for _ in range(20)
        )
        events.append(_completed())
        completed, output, _, _ = _run(events, workspace, heartbeat=0, max_updates=6)

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertLessEqual(len(output), 6)
        self.assertEqual(output[0]["event"], "init")
        self.assertEqual(output[0]["workspace"], "verified")
        self.assertTrue(any(item["event"] == "phase" and item.get("tool") == "rg" for item in output))
        self.assertTrue(any(item["event"] == "heartbeat" for item in output))
        self.assertEqual(output[-1]["event"], "final")
        self.assertNotIn(b"SECRET_TOOL_OUTPUT_DO_NOT_FORWARD", completed.stdout)

    def test_structured_completed_is_accepted(self) -> None:
        workspace = ROOT.resolve()
        completed, output, state, _ = _run([_init(workspace), _completed()], workspace)

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(output[-1]["protocol"], "completed")
        self.assertEqual(output[-1]["outcome"], "completed")
        self.assertIn("tests/fixture-result.txt", output[-1]["evidence"])
        self.assertNotIn(b"SENSITIVE_RAW_RESPONSE_SHOULD_NOT_APPEAR", completed.stdout)
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "completed")
        state.unlink(missing_ok=True)

    def test_real_stream_envelope_is_unwrapped(self) -> None:
        workspace = ROOT.resolve()
        init = _init(workspace)
        nested_init = {"event": "init", "conversation_id": "fixture-conversation", "init": init}
        nested_step = {
            "event": "step_update",
            "step_update": {
                "conversation_id": "fixture-conversation",
                "step_type": "tool",
                "tool_name": "view_file",
                "tool_info": {"output": "PRIVATE_TOOL_OUTPUT"},
            },
        }
        result = _completed()
        nested_result = {"event": "result", "result": {**result, "conversation_id": "fixture-conversation"}}
        completed, output, _, _ = _run([nested_init, nested_step, nested_result], workspace)

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(output[-1]["protocol"], "completed")
        self.assertTrue(any(item.get("tool") == "view_file" for item in output if item["event"] == "phase"))
        self.assertNotIn(b"PRIVATE_TOOL_OUTPUT", completed.stdout)

    def test_idle_stdin_still_emits_timer_heartbeat(self) -> None:
        workspace = ROOT.resolve()
        with tempfile.TemporaryDirectory(prefix="agy-reducer-idle-") as temp:
            command = [
                sys.executable,
                "-B",
                str(REDUCER),
                "--task-id",
                "fixture-task",
                "--task-mode",
                "REVIEW",
                "--execution-profile",
                "REVIEW_EXTERNAL",
                "--workspace",
                str(workspace),
                "--heartbeat-seconds",
                "0.1",
                "--max-updates",
                "4",
            ]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert process.stdin is not None
            time.sleep(0.5)
            process.stdin.close()
            process.stdin = None
            process.wait(timeout=5)
            stdout = process.stdout.read() if process.stdout is not None else b""
            stderr = process.stderr.read() if process.stderr is not None else b""
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            output = [json.loads(line) for line in stdout.splitlines() if line.strip()]

        self.assertEqual(process.returncode, 2, stderr.decode())
        self.assertTrue(any(item["event"] == "heartbeat" for item in output))
        self.assertEqual(output[-1]["code"], "missing_final")

    def test_terminal_result_exits_without_waiting_for_stdin_eof(self) -> None:
        workspace = ROOT.resolve()
        command = [
            sys.executable,
            "-B",
            str(REDUCER),
            "--task-id",
            "fixture-task",
            "--task-mode",
            "REVIEW",
            "--execution-profile",
            "REVIEW_EXTERNAL",
            "--workspace",
            str(workspace),
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
        )
        assert process.stdin is not None
        process.stdin.write(_event_lines([_init(workspace), _completed()]))
        process.stdin.flush()
        try:
            process.wait(timeout=2)
        finally:
            process.stdin.close()
        stdout = process.stdout.read() if process.stdout is not None else b""
        stderr = process.stderr.read() if process.stderr is not None else b""
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        output = [json.loads(line) for line in stdout.splitlines() if line.strip()]

        self.assertEqual(process.returncode, 0, stderr.decode())
        self.assertEqual(output[-1]["protocol"], "completed")

    def test_structured_blocked_requires_and_reports_reasons(self) -> None:
        workspace = ROOT.resolve()
        blocked = {
            "type": "result",
            "status": "SUCCESS",
            "structured_output": {
                "task_id": "fixture-task",
                "outcome": "blocked",
                "summary": "The task could not start.",
                "reason": "The required source is unavailable.",
                "missing": ["source URL"],
                "next_steps": ["Provide the source URL."],
                "evidence": ["init: source probe failed"],
            },
        }
        completed, output, _, _ = _run([_init(workspace), blocked], workspace)

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(output[-1]["protocol"], "blocked")
        self.assertEqual(output[-1]["reason"], "The required source is unavailable.")
        self.assertEqual(output[-1]["missing"], ["source URL"])
        self.assertEqual(output[-1]["next_steps"], ["Provide the source URL."])

    def test_required_exact_tools_fail_fast_when_missing(self) -> None:
        workspace = ROOT.resolve()
        init = _init(workspace, tools=["view_file", "grep_search"])
        completed, output, _, _ = _run(
            [init, _completed()],
            workspace,
            required_tools=["view_file", "search_web"],
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[0]["available_tools"], ["view_file"])
        self.assertEqual(output[0]["missing_tools"], ["search_web"])
        self.assertTrue(any(item.get("code") == "capability_missing" for item in output if item["event"] == "warning"))
        self.assertEqual(output[-1]["code"], "capability_missing")

    def test_execution_profile_and_resume_identity_are_enforced(self) -> None:
        workspace = ROOT.resolve()
        local_init = _init(workspace)
        local_init["expanded_commands"] = [{"name": "plan", "type": "system"}]
        completed, output, _, _ = _run(
            [local_init, _completed()],
            workspace,
            execution_profile="REVIEW_LOCAL",
            expected_conversation="fixture-conversation",
            expected_project="fixture-project",
            expected_permission_mode="request-review",
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(output[0]["execution_profile"], "REVIEW_LOCAL")
        self.assertTrue(output[0]["plan_active"])

        completed, output, _, _ = _run(
            [_init(workspace), _completed()],
            workspace,
            execution_profile="REVIEW_LOCAL",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["code"], "plan_not_active")

        completed, output, _, _ = _run(
            [_init(workspace), _completed()],
            workspace,
            expected_conversation="different-conversation",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["code"], "conversation_mismatch")

        completed, output, _, _ = _run(
            [_init(workspace), _completed()],
            workspace,
            expected_permission_mode="always-proceed",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["code"], "permission_mode_mismatch")

    def test_bare_blocked_is_protocol_error(self) -> None:
        workspace = ROOT.resolve()
        bare = {"type": "result", "status": "SUCCESS", "response": "BLOCKED"}
        completed, output, _, _ = _run([_init(workspace), bare], workspace)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["protocol"], "protocol_error")
        self.assertEqual(output[-1]["code"], "bare_blocked")

    def test_malformed_or_unstructured_final_is_protocol_error(self) -> None:
        workspace = ROOT.resolve()
        unstructured = {"type": "result", "status": "SUCCESS", "response": "{}"}
        completed, output, _, _ = _run([_init(workspace), unstructured], workspace)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["protocol"], "protocol_error")
        self.assertEqual(output[-1]["code"], "missing_structured_output")

    def test_malformed_prefix_cannot_be_rescued_by_valid_final(self) -> None:
        workspace = ROOT.resolve()
        completed, output, _, _ = _run(
            [_init(workspace), _completed()],
            workspace,
            raw_prefix=b"not-json\n",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["protocol"], "protocol_error")
        self.assertEqual(output[-1]["code"], "malformed_stream")

    def test_workspace_binding_and_relative_workspace_fail_closed(self) -> None:
        workspace = ROOT.resolve()
        mismatched = _init(workspace / "not-the-expected-workspace")
        mismatched["cwd"] = str(workspace)
        completed, output, _, _ = _run([mismatched, _completed()], workspace / "not-the-expected-workspace")

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["protocol"], "protocol_error")
        self.assertEqual(output[-1]["code"], "workspace_binding_mismatch")

        completed, output, _, _ = _run([{"type": "init", "cwd": "."}, _completed()], Path("relative"))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["code"], "workspace_not_absolute")

    def test_untrusted_nested_metadata_cannot_prove_workspace_binding(self) -> None:
        workspace = ROOT.resolve()
        init = _init(workspace / "wrong")
        init["metadata"] = {"allowed_directories": [str(workspace)]}
        completed, output, _, _ = _run([init, _completed()], workspace)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(output[-1]["code"], "workspace_binding_mismatch")

    def test_raw_log_and_stdout_are_bounded(self) -> None:
        workspace = ROOT.resolve()
        events: List[Dict[str, Any]] = [_init(workspace)]
        events.extend({"type": "step_update", "step_type": "tool_call", "tool_output": "X" * 5000} for _ in range(30))
        events.append(_completed())
        completed, output, _, raw = _run(
            events,
            workspace,
            heartbeat=0,
            max_updates=12,
            max_output_bytes=512,
            raw_log_bytes=1024,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertLessEqual(len(completed.stdout), 512)
        self.assertLessEqual(raw.stat().st_size, 1024)
        self.assertNotIn(b"X" * 100, completed.stdout)
        self.assertEqual(output[-1]["event"], "final")
        raw.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
