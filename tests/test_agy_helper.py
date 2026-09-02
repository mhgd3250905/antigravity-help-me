"""Contract tests for the command-first agy helper.

The fake producer is an ordinary subprocess. It implements only the small
surface used by the helper and never invokes a real Antigravity installation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Optional
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "agy_helper.py"


FAKE_AGY = r'''
import json
import os
import pathlib
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("agy 1.1.22")
    raise SystemExit(0)
if "--help" in args:
    print("""Usage: agy
  --add-dir
  --mode (accept-edits, plan)
  -p, --print
  --model
  --effort (low|medium|high)
  --output-format (text, json, stream-json)
  --json-schema
  --print-timeout
  --conversation
  --dangerously-skip-permissions
""")
    raise SystemExit(0)
if args[:3] == ["--output-format", "json", "models"]:
    print(json.dumps({"command": {"data": {"models": [{"id": "gemini-3.7-flash-high"}]}}}))
    raise SystemExit(0)

workspace = pathlib.Path(args[args.index("--add-dir") + 1])
prompt = args[args.index("-p") + 1]
task_path = pathlib.Path(prompt.split('"')[1])
task_id = task_path.parent.name
task_text = task_path.read_text(encoding="utf-8")
tools = ["view_file", "search_web"]
print(json.dumps({
    "type": "init",
    "cwd": str(workspace),
    "model": "gemini-3.7-flash-high",
    "permission_mode": "request-review",
    "conversation_id": "fake-conversation",
    "tools": tools,
    "expanded_commands": [{"name": "plan"}] if "执行配置：REVIEW_LOCAL" in task_text else [],
}), flush=True)
result = {
    "task_id": task_id,
    "outcome": "completed",
    "summary": "fake completed",
    "reason": "",
    "missing": [],
    "next_steps": [],
    "evidence": ["fake evidence"],
}
if os.environ.get("FAKE_AGY_BLOCKED"):
    result.update({
        "outcome": "blocked",
        "summary": "fake blocked",
        "reason": "a required input is unavailable",
        "missing": ["required input"],
        "next_steps": ["provide the required input"],
    })
if "preset: verify" in task_text and result["outcome"] == "completed":
    result["verdict"] = "pass"
if os.environ.get("FAKE_AGY_BURST_TOOLS"):
    count = int(os.environ["FAKE_AGY_BURST_TOOLS"])
    for i in range(count):
        print(json.dumps({
            "type": "step",
            "step_index": str(i + 1),
            "tool_call": {"name": "view_file", "id": f"call-{i}"},
        }), flush=True)
if os.environ.get("FAKE_AGY_PRE_FINAL_SLEEP"):
    time.sleep(float(os.environ["FAKE_AGY_PRE_FINAL_SLEEP"]))
print(json.dumps({"type": "result", "status": "SUCCESS", "structured_output": result}), flush=True)
if os.environ.get("FAKE_AGY_POST_FINAL_SLEEP"):
    time.sleep(float(os.environ["FAKE_AGY_POST_FINAL_SLEEP"]))
raise SystemExit(int(os.environ.get("FAKE_AGY_EXIT", "0")))
'''


BATCH_FAKE_AGY = r'''
import json
import os
import pathlib
import re
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("agy 1.1.22")
    raise SystemExit(0)
if "--help" in args:
    print("""Usage: agy
  --add-dir
  --mode (accept-edits, plan)
  -p, --print
  --model
  --effort (low|medium|high)
  --output-format (text, json, stream-json)
  --json-schema
  --print-timeout
  --conversation
  --dangerously-skip-permissions
""")
    raise SystemExit(0)
if args[:3] == ["--output-format", "json", "models"]:
    print(json.dumps({"command": {"data": {"models": [{"id": "gemini-3.7-flash-high"}]}}}))
    raise SystemExit(0)

workspace = pathlib.Path(args[args.index("--add-dir") + 1])
prompt = args[args.index("-p") + 1]
task_path = pathlib.Path(prompt.split('"')[1])
task_id = task_path.parent.name
task_text = task_path.read_text(encoding="utf-8")
goal = next((line.split("：", 1)[1] for line in task_text.splitlines() if line.startswith("- 目标：")), "")
control = os.environ.get("FAKE_BATCH_CONTROL")
control_path = pathlib.Path(control) if control else None

def record(kind):
    if not control_path:
        return
    control_path.mkdir(parents=True, exist_ok=True)
    (control_path / (kind + "-" + task_id + ".json")).write_text(
        json.dumps({"kind": kind, "task_id": task_id, "goal": goal, "time": time.monotonic()}, ensure_ascii=False),
        encoding="utf-8",
    )

record("start")
print(json.dumps({
    "type": "init",
    "cwd": str(workspace),
    "model": "gemini-3.7-flash-high",
    "permission_mode": "request-review",
    "conversation_id": "batch-fake-" + task_id,
    "tools": [],
    "expanded_commands": [{"name": "plan"}] if "执行配置：REVIEW_LOCAL" in task_text else [],
}), flush=True)

sleep_match = re.search(r"sleep=([0-9.]+)", goal)
duration = float(sleep_match.group(1)) if sleep_match else float(os.environ.get("FAKE_BATCH_SLEEP", "0.25"))
if "timeout" not in goal:
    time.sleep(duration)
    outcome = "blocked" if "blocked" in goal else "completed"
    result = {
        "task_id": task_id,
        "outcome": outcome,
        "summary": "fake batch " + outcome,
        "reason": "blocked by fixture" if outcome == "blocked" else "",
        "missing": ["fixture input"] if outcome == "blocked" else [],
        "next_steps": ["provide fixture input"] if outcome == "blocked" else [],
        "evidence": ["batch fake evidence"],
    }
    if "verify" in task_text and outcome == "completed":
        result["verdict"] = "pass"
    record("end")
    print(json.dumps({"type": "result", "status": "SUCCESS", "structured_output": result}, ensure_ascii=False), flush=True)
    raise SystemExit(7 if "fail" in goal else 0)
time.sleep(10)
'''


def _make_fake_agy(folder: Path) -> Path:
    script = folder / "fake_agy.py"
    script.write_text(textwrap.dedent(FAKE_AGY), encoding="utf-8")
    return script


def _make_batch_fake_agy(folder: Path) -> Path:
    script = folder / "batch_fake_agy.py"
    script.write_text(textwrap.dedent(BATCH_FAKE_AGY), encoding="utf-8")
    return script


def _base_request(workspace: Path) -> dict:
    return {
        "workspace": str(workspace),
        "goal": "审查登录流程并给出证据",
        "scope": ["src/**", "tests/**"],
        "acceptance": ["每项发现包含文件和复现证据"],
    }


def _batch_job(workspace: Path, goal: str, preset: str = "review-local") -> dict:
    request = {
        "workspace": str(workspace),
        "goal": goal,
        "scope": ["src/**"],
        "acceptance": ["fake task reaches a terminal result"],
    }
    if preset in {"change", "repair"}:
        request.update({"allowed_changes": ["src/**"], "authorization": "fixture authorization"})
    if preset == "repair":
        request.update({"parent_task_id": "old-task", "failure": "fixture failure"})
    if preset == "verify":
        request["subject"] = "fixture subject"
    return {"preset": preset, "request": request}


def _read_batch_events(control: Path) -> list[dict]:
    if not control.exists():
        return []
    return [json.loads(path.read_text(encoding="utf-8")) for path in control.glob("*.json")]


class HelperContractTests(unittest.TestCase):
    def _invoke_batch(
        self,
        agy: Path,
        request: dict,
        *,
        control: Optional[Path] = None,
        run_timeout: str = "2",
        extra_env: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        environment = os.environ.copy()
        if control is not None:
            environment["FAKE_BATCH_CONTROL"] = str(control)
        if extra_env:
            environment.update(extra_env)
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "batch",
                "--request-stdin",
                "--agy",
                str(agy),
                "--run-timeout",
                run_timeout,
            ],
            input=json.dumps(request, ensure_ascii=False) + "\n",
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_batch_max_parallel_is_bounded_and_fourth_runnable_job_queues(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-cap-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            request = {
                "batch_id": "batch-cap-test",
                "jobs": [_batch_job(workspace, f"job-{index} sleep=0.25") for index in range(4)],
            }
            process = self._invoke_batch(agy, request, control=control)
            self.assertEqual(process.returncode, 0, process.stderr)
            lines = [line for line in process.stdout.splitlines() if line.strip()]
            events = [json.loads(line) for line in lines]
            self.assertEqual(len(lines), len(events), "every output line must be one JSON object")
            summary = events[-1]
            self.assertEqual(summary["event"], "batch")
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["batch_id"], "batch-cap-test")
            self.assertEqual(summary["job_count"], 4)
            lane_events = [event for event in events if event.get("event") != "batch"]
            self.assertTrue(lane_events)
            self.assertTrue(all(event["batch_id"] == "batch-cap-test" for event in lane_events))
            self.assertEqual(
                {event["job_id"] for event in events if event.get("event") == "run"},
                {f"job-{index + 1:03d}" for index in range(4)},
            )
            for job in summary["jobs"]:
                self.assertIsInstance(job["producer_pid"], int)
                self.assertIsInstance(job["reducer_pid"], int)
                self.assertTrue(job["deadline_at"])
                launch = json.loads((Path(job["task_dir"]) / "launch.json").read_text(encoding="utf-8"))
                self.assertEqual(launch["batch_id"], "batch-cap-test")
                self.assertEqual(launch["job_id"], job["job_id"])

            fixture_events = _read_batch_events(control)
            starts = [event for event in fixture_events if event["kind"] == "start"]
            ends = [event for event in fixture_events if event["kind"] == "end"]
            self.assertEqual(len(starts), 4)
            self.assertEqual(len(ends), 4)
            first_end = min(event["time"] for event in ends)
            self.assertGreaterEqual(sum(event["time"] < first_end for event in starts), 3)
            self.assertGreaterEqual(
                max(event["time"] for event in starts),
                first_end,
                "the fourth runnable job must wait for an active lane",
            )

            active = 0
            maximum = 0
            for event in sorted(fixture_events, key=lambda item: item["time"]):
                active += 1 if event["kind"] == "start" else -1
                maximum = max(maximum, active)
            self.assertLessEqual(maximum, 3)

    def test_batch_max_parallel_accepts_only_one_through_three(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-validation-") as raw:
            workspace = Path(raw) / "workspace"
            workspace.mkdir()
            base = {"jobs": [_batch_job(workspace, "one")]}
            self.assertEqual(agy_helper._validate_batch_request(base)["max_parallel"], 3)
            for value in (0, 4, True, 1.5, "2"):
                with self.subTest(max_parallel=value):
                    with self.assertRaises(agy_helper.HelperError) as context:
                        agy_helper._validate_batch_request({**base, "max_parallel": value})
                    self.assertEqual(context.exception.code, agy_helper.EXIT_REQUEST_INVALID)

    def test_batch_request_file_accepts_bounded_utf8_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-file-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            agy = _make_batch_fake_agy(fake_dir)
            request_file = root / "batch.json"
            request_file.write_text(
                json.dumps(
                    {"batch_id": "batch-file-test", "jobs": [_batch_job(workspace, "文件任务 sleep=0.05")]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            process = subprocess.run(
                [
                    sys.executable,
                    str(HELPER),
                    "batch",
                    "--request-file",
                    str(request_file),
                    "--agy",
                    str(agy),
                    "--run-timeout",
                    "2",
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
            self.assertEqual(events[-1]["batch_id"], "batch-file-test")
            task_path = Path(events[-1]["jobs"][0]["task_path"])
            self.assertIn("文件任务", task_path.read_text(encoding="utf-8"))

    def test_batch_reads_share_overlapping_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-read-share-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            request = {"jobs": [_batch_job(workspace, f"read-{index} sleep=0.3") for index in range(3)]}
            process = self._invoke_batch(agy, request, control=control)
            self.assertEqual(process.returncode, 0, process.stderr)
            fixture_events = _read_batch_events(control)
            starts = [event for event in fixture_events if event["kind"] == "start"]
            ends = [event for event in fixture_events if event["kind"] == "end"]
            self.assertEqual(len(starts), 3)
            self.assertLess(max(event["time"] for event in starts), min(event["time"] for event in ends))

    def test_batch_write_is_exclusive_and_does_not_starve_behind_reads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-locks-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            child_workspace = workspace / "child"
            child_workspace.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            jobs = [_batch_job(child_workspace, f"read-{index} sleep=0.25") for index in range(6)]
            jobs.append(_batch_job(workspace, "write sleep=0.15", "change"))
            process = self._invoke_batch(agy, {"jobs": jobs}, control=control)
            self.assertEqual(process.returncode, 0, process.stderr)
            fixture_events = _read_batch_events(control)
            intervals = {}
            for event in fixture_events:
                intervals.setdefault(event["goal"].split(" ", 1)[0], {})[event["kind"]] = event["time"]
            write_interval = intervals["write"]
            read_intervals = [value for key, value in intervals.items() if key.startswith("read-")]
            self.assertTrue(read_intervals)
            for interval in read_intervals:
                self.assertTrue(
                    write_interval["end"] <= interval["start"] or interval["end"] <= write_interval["start"],
                    "overlapping read/write workspaces must not run concurrently",
                )
            self.assertLess(
                write_interval["start"],
                max(interval["end"] for interval in read_intervals),
                "a queued write must be admitted before all overlapping reads finish",
            )

    def test_batch_non_overlapping_writes_can_run_concurrently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-write-share-") as raw:
            root = Path(raw)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            request = {
                "jobs": [
                    _batch_job(left, "left-write sleep=0.35", "change"),
                    _batch_job(right, "right-write sleep=0.35", "change"),
                ]
            }
            process = self._invoke_batch(agy, request, control=control)
            self.assertEqual(process.returncode, 0, process.stderr)
            fixture_events = _read_batch_events(control)
            starts = sorted(event["time"] for event in fixture_events if event["kind"] == "start")
            ends = sorted(event["time"] for event in fixture_events if event["kind"] == "end")
            self.assertEqual(len(starts), 2)
            self.assertLess(max(starts), min(ends))

    def test_batch_skips_a_locked_workspace_for_other_runnable_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-scan-") as raw:
            root = Path(raw)
            left = root / "left"
            right = root / "right"
            left.mkdir()
            right.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            request = {
                "max_parallel": 3,
                "jobs": [
                    _batch_job(left, "left-first sleep=0.35", "change"),
                    _batch_job(left, "left-second sleep=0.35", "change"),
                    _batch_job(right, "right-write sleep=0.15", "change"),
                ],
            }
            process = self._invoke_batch(agy, request, control=control)
            self.assertEqual(process.returncode, 0, process.stderr)
            fixture_events = _read_batch_events(control)
            intervals = {}
            for event in fixture_events:
                intervals.setdefault(event["goal"].split(" ", 1)[0], {})[event["kind"]] = event["time"]
            self.assertLess(intervals["right-write"]["start"], intervals["left-first"]["end"])
            self.assertLessEqual(intervals["left-first"]["end"], intervals["left-second"]["start"])

    def test_batch_lane_failure_and_timeout_do_not_stop_other_lanes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-isolation-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            request = {
                "max_parallel": 3,
                "jobs": [
                    _batch_job(workspace, "ok sleep=0.3"),
                    _batch_job(workspace, "fail sleep=0.1"),
                    _batch_job(workspace, "timeout"),
                    _batch_job(workspace, "blocked sleep=0.1"),
                ],
            }
            process = self._invoke_batch(agy, request, control=control, run_timeout="1")
            self.assertNotEqual(process.returncode, 0)
            events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
            summary = events[-1]
            self.assertEqual(summary["status"], "failed")
            statuses = {job["job_id"]: job["status"] for job in summary["jobs"]}
            self.assertEqual(statuses["job-001"], "completed")
            self.assertEqual(statuses["job-002"], "producer_failed")
            self.assertEqual(statuses["job-003"], "timeout")
            self.assertIn(statuses["job-004"], {"completed", "blocked"})
            self.assertEqual(summary["jobs_completed"] + summary["jobs_blocked"], 2)

    def test_batch_cancellation_terminates_active_lanes_and_marks_pending_jobs(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-batch-cancel-") as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_dir = root / "fake"
            fake_dir.mkdir()
            control = root / "control"
            agy = _make_batch_fake_agy(fake_dir)
            request = {"jobs": [_batch_job(workspace, f"cancel-{index} timeout") for index in range(5)]}
            cancel_event = threading.Event()
            timer = threading.Timer(0.25, cancel_event.set)
            output = StringIO()
            timer.start()
            try:
                with mock.patch.dict(os.environ, {"FAKE_BATCH_CONTROL": str(control)}, clear=False):
                    with redirect_stdout(output):
                        code, summary = agy_helper._dispatch_batch(
                            request,
                            agy=str(agy),
                            skip_preflight=True,
                            run_timeout=30,
                            cancel_event=cancel_event,
                        )
            finally:
                timer.cancel()
            self.assertNotEqual(code, 0)
            self.assertEqual(summary["status"], "cancelled")
            self.assertEqual(summary["jobs_cancelled"], 5)
            self.assertTrue(all(job["status"] == "cancelled" for job in summary["jobs"]))
            for job in summary["jobs"]:
                if job.get("task_dir"):
                    task_dir = Path(job["task_dir"])
                    self.assertTrue((task_dir / "producer-exit.txt").exists())
                    self.assertTrue((task_dir / "reducer-exit.txt").exists())
    def test_public_run_parser_has_no_override_or_preflight_bypass_flags(self) -> None:
        from scripts import agy_helper

        parser = agy_helper._build_parser()
        base = ["run", "--preset", "review-local", "--request-stdin"]
        parsed = parser.parse_args(base)
        self.assertFalse(hasattr(parsed, "schema"))
        self.assertFalse(hasattr(parsed, "reducer"))
        self.assertFalse(hasattr(parsed, "skip_preflight"))
        for forbidden in ("--schema", "--reducer", "--skip-preflight"):
            with self.subTest(forbidden=forbidden):
                with mock.patch("sys.stderr", new=StringIO()):
                    with self.assertRaises(SystemExit) as context:
                        parser.parse_args([*base, forbidden, "fixture"] if forbidden != "--skip-preflight" else [*base, forbidden])
                self.assertEqual(context.exception.code, 2)

    def test_request_file_is_regular_and_actually_bounded(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-request-file-") as raw:
            folder = Path(raw)
            oversized = folder / "oversized.json"
            oversized.write_bytes(b"x" * (agy_helper.MAX_REQUEST_BYTES + 1))
            args = type("RequestArgs", (), {"request_stdin": False, "request_file": str(oversized)})()
            with self.assertRaises(agy_helper.HelperError) as context:
                agy_helper._read_request(args)
            self.assertEqual(context.exception.code, agy_helper.EXIT_REQUEST_INVALID)
            self.assertIn("bounded input limit", str(context.exception))

            args.request_file = str(folder)
            with self.assertRaises(agy_helper.HelperError) as context:
                agy_helper._read_request(args)
            self.assertEqual(context.exception.code, agy_helper.EXIT_REQUEST_INVALID)
            self.assertIn("regular file", str(context.exception))

    def test_doctor_success_and_missing_model_are_machine_classified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-doctor-") as raw:
            folder = Path(raw)
            agy = _make_fake_agy(folder)
            success = subprocess.run(
                [sys.executable, str(HELPER), "doctor", "--json", "--agy", str(agy)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            result = json.loads(success.stdout)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["compatibility"], "tested")
            self.assertEqual(result["problems"], [])
            self.assertTrue(result["models"]["available"])
            self.assertTrue(all(result["help"]["flags"].values()))

            # Untested version with complete capabilities must be ready with compatible_unverified, not blocked.
            script_new_ver = folder / "fake_new_ver.py"
            script_new_ver.write_text(
                (folder / "fake_agy.py").read_text(encoding="utf-8").replace("1.1.22", "1.1.23", 1),
                encoding="utf-8",
            )
            untested = subprocess.run(
                [sys.executable, str(HELPER), "doctor", "--json", "--agy", str(script_new_ver)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            untested_result = json.loads(untested.stdout)
            self.assertEqual(untested.returncode, 0, untested.stderr)
            self.assertEqual(untested_result["status"], "ready")
            self.assertEqual(untested_result["compatibility"], "compatible_unverified")
            self.assertEqual(untested_result["problems"], [])

            script = folder / "fake_no_model.py"
            script.write_text(
                (folder / "fake_agy.py").read_text(encoding="utf-8").replace(
                    "gemini-3.7-flash-high", "other-model", 1
                ),
                encoding="utf-8",
            )
            missing = script
            failure = subprocess.run(
                [sys.executable, str(HELPER), "doctor", "--json", "--agy", str(missing)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            failed_result = json.loads(failure.stdout)
            self.assertNotEqual(failure.returncode, 0)
            self.assertIn("model_unavailable", failed_result["problems"])

    def test_cli_stdout_is_utf8_with_non_utf8_default_encoding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-stdout-") as raw:
            folder = Path(raw) / "中文"
            folder.mkdir()
            agy = _make_fake_agy(folder)
            environment = os.environ.copy()
            environment.pop("PYTHONUTF8", None)
            environment["PYTHONIOENCODING"] = "gbk"
            process = subprocess.run(
                [sys.executable, str(HELPER), "doctor", "--json", "--agy", str(agy)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", errors="replace"))
            output = process.stdout.decode("utf-8")
            result = json.loads(output)
            self.assertEqual(result["status"], "ready")
            self.assertIn("中文", result["agy"]["path"])

    def test_stdin_and_file_requests_create_contracts_without_user_text_in_argv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-run-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            request = _base_request(root)
            request["goal"] += "：" + "超长正文" * 30000
            request["tool_budget"] = {"max_updates": 7}
            request["required_tools"] = ["view_file"]
            process = subprocess.run(
                [sys.executable, str(HELPER), "run", "--preset", "review-local", "--request-stdin", "--agy", str(agy)],
                input=json.dumps(request, ensure_ascii=False) + "\n",
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            events = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
            summary = events[-1]
            task_path = Path(summary["task_path"])
            self.assertTrue(task_path.is_file())
            launch = json.loads((task_path.parent / "launch.json").read_text(encoding="utf-8"))
            self.assertNotIn("超长正文", json.dumps(launch, ensure_ascii=False))
            task_text = task_path.read_text(encoding="utf-8")
            self.assertIn("超长正文", task_text)
            self.assertIn("执行配置：REVIEW_LOCAL", task_text)
            self.assertIn("--mode", launch["agy_argv"])
            self.assertIn("plan", launch["agy_argv"])
            self.assertIn("view_file", launch["reducer_argv"])
            self.assertEqual(launch["reducer_argv"][launch["reducer_argv"].index("--max-updates") + 1], "7")

            # Minimal request without optional tool_budget/read_allowlist/stop_conditions has no default restrictions.
            request_file = Path(raw) / "request.json"
            request_file.write_text(json.dumps(_base_request(root), ensure_ascii=False), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, str(HELPER), "run", "--preset", "review-external", "--request-file", str(request_file), "--agy", str(agy)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            summary = json.loads(process.stdout.splitlines()[-1])
            task_contract = Path(summary["task_path"]).read_text(encoding="utf-8")
            launch = json.loads((Path(summary["task_path"]).parent / "launch.json").read_text(encoding="utf-8"))
            self.assertNotIn("--mode", launch["agy_argv"])
            self.assertNotIn("--required-tool", launch["reducer_argv"])
            self.assertNotIn("--max-updates", launch["reducer_argv"])
            self.assertNotIn("工具调用预算", task_contract)
            self.assertNotIn("读取/检查 allowlist：", task_contract)
            self.assertNotIn("停止条件：", task_contract)
            self.assertIn("- （无额外工具）", task_contract)

    def test_all_presets_map_to_fixed_profiles_and_extra_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-presets-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            requests = {
                "review-local": _base_request(root),
                "review-external": _base_request(root),
                "change": {**_base_request(root), "allowed_changes": ["src/auth.py"], "authorization": "user authorized this bounded change"},
                "repair": {**_base_request(root), "parent_task_id": "old-task", "failure": "the prior test still fails", "allowed_changes": ["src/auth.py"], "authorization": "user authorized this bounded repair"},
                "verify": {**_base_request(root), "subject": "the current working tree"},
            }
            expected = {
                "review-local": ("REVIEW_LOCAL", "REVIEW"),
                "review-external": ("REVIEW_EXTERNAL", "REVIEW"),
                "change": ("CHANGE", "CHANGE"),
                "repair": ("CHANGE", "CHANGE"),
                "verify": ("REVIEW_LOCAL", "REVIEW"),
            }
            for preset, request in requests.items():
                with self.subTest(preset=preset):
                    process = subprocess.run(
                        [sys.executable, str(HELPER), "run", "--preset", preset, "--request-stdin", "--agy", str(agy)],
                        input=json.dumps(request, ensure_ascii=False) + "\n",
                        cwd=ROOT,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        check=False,
                    )
                    self.assertEqual(process.returncode, 0, process.stderr)
                    summary = json.loads(process.stdout.splitlines()[-1])
                    task_text = Path(summary["task_path"]).read_text(encoding="utf-8")
                    launch = json.loads((Path(summary["task_path"]).parent / "launch.json").read_text(encoding="utf-8"))
                    self.assertIn(f"执行配置：{expected[preset][0]}", task_text)
                    self.assertIn(f"MODE: {expected[preset][1]}", task_text)
                    self.assertIn(expected[preset][0], launch["reducer_argv"])
                    if preset in {"repair", "verify"}:
                        self.assertIn("新 conversation", task_text)
                    if preset == "verify":
                        self.assertEqual(summary["verdict"], "pass")
                        self.assertIn("--require-verdict", launch["reducer_argv"])

    def test_task_creation_refuses_to_overwrite_existing_id(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-collision-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            request = agy_helper._validate_request(_base_request(root), "review-local")
            with mock.patch.object(agy_helper, "_generate_task_id", return_value="fixed-task"):
                agy_helper._create_task(request, "review-local")
                task_file = root / ".antigravity-help-me" / "tasks" / "fixed-task" / "TASK.md"
                original = task_file.read_text(encoding="utf-8")
                with self.assertRaises(agy_helper.HelperError) as context:
                    agy_helper._create_task(request, "review-local")
                self.assertEqual(context.exception.code, agy_helper.EXIT_TASK_EXISTS)
                self.assertEqual(task_file.read_text(encoding="utf-8"), original)

    def test_repair_requires_explicit_authorization_and_budget_is_strictly_typed(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-validation-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            repair = {
                **_base_request(root),
                "parent_task_id": "old-task",
                "failure": "a bounded failure",
                "allowed_changes": ["src/auth.py"],
            }
            with self.assertRaises(agy_helper.HelperError) as context:
                agy_helper._validate_request(repair, "repair")
            self.assertIn("authorization", str(context.exception))

            valid = {**repair, "authorization": "explicit user authorization"}
            for budget in (
                {"unknown": 1},
                {"max_total_calls": True},
                {"max_calls_per_tool": 0},
                {"max_updates": -1},
                {"max_total_calls": 1001},
                {"stop_when_exhausted": 1},
            ):
                with self.subTest(budget=budget):
                    with self.assertRaises(agy_helper.HelperError):
                        agy_helper._validate_request({**valid, "tool_budget": budget}, "repair")
            normalized = agy_helper._validate_request(
                {**valid, "tool_budget": {"max_updates": 7, "max_total_calls": 30, "stop_when_exhausted": True}},
                "repair",
            )
            self.assertEqual(normalized["tool_budget"]["max_updates"], 7)
            self.assertEqual(normalized["tool_budget"]["max_total_calls"], 30)
            self.assertEqual(normalized["tool_budget"]["stop_when_exhausted"], True)

            # tool_budget is optional; when omitted, normalized does not have tool_budget
            omitted = agy_helper._validate_request(valid, "repair")
            self.assertNotIn("tool_budget", omitted)

            self.assertEqual(agy_helper._positive_run_timeout("1"), 1.0)
            for timeout in ("0", "7201", "not-a-number"):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(Exception):
                        agy_helper._positive_run_timeout(timeout)

    def test_producer_failure_is_not_accepted_even_with_valid_final(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-exit-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            environment = os.environ.copy()
            environment["FAKE_AGY_EXIT"] = "7"
            process = subprocess.run(
                [sys.executable, str(HELPER), "run", "--preset", "review-local", "--request-stdin", "--agy", str(agy)],
                input=json.dumps(_base_request(root), ensure_ascii=False) + "\n",
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            summary = json.loads(process.stdout.splitlines()[-1])
            self.assertEqual(summary["status"], "producer_failed")
            self.assertEqual(summary["producer_exit_code"], 7)
            task_dir = Path(summary["task_dir"])
            self.assertEqual((task_dir / "producer-exit.txt").read_text(encoding="utf-8").strip(), "7")
            self.assertTrue((task_dir / "producer-stderr.log").exists())

    def test_compact_init_is_forwarded_before_producer_final(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-live-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            environment = os.environ.copy()
            environment["FAKE_AGY_PRE_FINAL_SLEEP"] = "0.5"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(HELPER),
                    "run",
                    "--preset",
                    "review-local",
                    "--request-stdin",
                    "--agy",
                    str(agy),
                ],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write((json.dumps(_base_request(root), ensure_ascii=False) + "\n").encode("utf-8"))
            process.stdin.flush()
            first_line = process.stdout.readline()
            self.assertTrue(first_line)
            first_event = json.loads(first_line.decode("utf-8"))
            self.assertEqual(first_event["event"], "init")
            self.assertIsNone(process.poll(), "init must be visible while producer is still running")
            process.stdin.close()
            remaining = process.stdout.read()
            process.wait(timeout=5)
            events = [first_event] + [json.loads(line) for line in remaining.splitlines() if line.strip()]
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            self.assertEqual(process.returncode, 0)
            self.assertEqual(events[-1]["event"], "run")

    def test_blocked_verify_keeps_blocked_semantics_without_verdict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agy-helper-blocked-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            environment = os.environ.copy()
            environment["FAKE_AGY_BLOCKED"] = "1"
            process = subprocess.run(
                [sys.executable, str(HELPER), "run", "--preset", "verify", "--request-stdin", "--agy", str(agy)],
                input=json.dumps({**_base_request(root), "subject": "current tree"}, ensure_ascii=False) + "\n",
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            summary = json.loads(process.stdout.splitlines()[-1])
            self.assertEqual(summary["status"], "blocked")
            self.assertNotIn("verdict", summary)
            self.assertEqual(summary["final"]["protocol"], "blocked")

    def test_post_final_producer_grace_is_bounded(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-grace-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            environment = os.environ.copy()
            # Keep the fake producer alive beyond the reducer's short
            # post-final drain so the helper's own producer grace path is
            # exercised deterministically.
            environment["FAKE_AGY_POST_FINAL_SLEEP"] = "1.0"
            output = StringIO()
            started = time.monotonic()
            with mock.patch.dict(os.environ, environment, clear=True):
                with mock.patch.object(agy_helper, "POST_FINAL_PRODUCER_GRACE_SECONDS", 0.05):
                    with redirect_stdout(output):
                        code, summary = agy_helper._dispatch(
                            _base_request(root),
                            "review-local",
                            agy=str(agy),
                            skip_preflight=True,
                            run_timeout=1,
                        )
            elapsed = time.monotonic() - started
            self.assertEqual(code, agy_helper.EXIT_DISPATCH_FAILED)
            self.assertEqual(summary["status"], "producer_grace_timeout")
            self.assertTrue(summary["producer_grace_timeout"])
            self.assertLess(elapsed, 0.9)
            self.assertIn("post-final grace", summary["evidence"][0])

    def test_explicit_constraints_rendering(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-constraints-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            req = {
                **_base_request(root),
                "required_tools": ["view_file", "search_web"],
                "read_allowlist": ["src/auth.py", "tests/test_auth.py"],
                "stop_conditions": ["stop on first error", "stop after 5 steps"],
                "tool_budget": {"max_total_calls": 50, "max_calls_per_tool": 10, "max_updates": 20, "stop_when_exhausted": False},
            }
            normalized = agy_helper._validate_request(req, "review-local")
            task_id, task_dir, task_path = agy_helper._create_task(normalized, "review-local")
            task_text = task_path.read_text(encoding="utf-8")
            self.assertIn("view_file", task_text)
            self.assertIn("search_web", task_text)
            self.assertIn("读取/检查 allowlist：", task_text)
            self.assertIn("src/auth.py", task_text)
            self.assertIn("停止条件：", task_text)
            self.assertIn("stop on first error", task_text)
            self.assertIn("工具调用预算", task_text)
            self.assertIn('"max_total_calls": 50', task_text)
            self.assertIn("不得读取未列入 allowlist 的路径", task_text)

    def test_preset_validation_requirements(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-reqs-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            base = _base_request(root)

            # change requires allowed_changes and authorization
            with self.assertRaises(agy_helper.HelperError) as ctx:
                agy_helper._validate_request(base, "change")
            self.assertIn("allowed_changes", str(ctx.exception))

            with self.assertRaises(agy_helper.HelperError) as ctx:
                agy_helper._validate_request({**base, "allowed_changes": ["src/a.py"]}, "change")
            self.assertIn("authorization", str(ctx.exception))

            # verify requires subject
            with self.assertRaises(agy_helper.HelperError) as ctx:
                agy_helper._validate_request(base, "verify")
            self.assertIn("subject", str(ctx.exception))

    def test_tty_echo_control_windows_enter_and_restore(self) -> None:
        from scripts import agy_helper

        mock_kernel32 = mock.MagicMock()
        mock_kernel32.GetStdHandle.return_value = 12345

        def fake_get_console_mode(handle, byref_mode):
            byref_mode._obj.value = 0x0007
            return 1

        mock_kernel32.GetConsoleMode.side_effect = fake_get_console_mode
        mock_kernel32.SetConsoleMode.return_value = 1

        with mock.patch("os.name", "nt"):
            with mock.patch("ctypes.windll.kernel32", mock_kernel32, create=True):
                # 1. Success path
                with agy_helper._temporary_no_echo_stdin():
                    mock_kernel32.SetConsoleMode.assert_called_once_with(12345, 0x0003)
                mock_kernel32.SetConsoleMode.assert_called_with(12345, 0x0007)

                # 2. Exception path
                mock_kernel32.SetConsoleMode.reset_mock()
                with self.assertRaises(RuntimeError):
                    with agy_helper._temporary_no_echo_stdin():
                        mock_kernel32.SetConsoleMode.assert_called_once_with(12345, 0x0003)
                        raise RuntimeError("simulated error")
                mock_kernel32.SetConsoleMode.assert_called_with(12345, 0x0007)

    def test_tty_echo_control_posix_enter_and_restore(self) -> None:
        from scripts import agy_helper

        mock_termios = mock.MagicMock()
        mock_termios.ECHO = 0x0008
        mock_termios.TCSADRAIN = 1
        orig_attr = [0, 0, 0, 0x000B, 0, 0, []]
        mock_termios.tcgetattr.side_effect = lambda fd: list(orig_attr)

        with mock.patch("os.name", "posix"):
            with mock.patch.dict("sys.modules", {"termios": mock_termios}):
                # 1. Success path
                with mock.patch("sys.stdin.fileno", return_value=0):
                    with agy_helper._temporary_no_echo_stdin():
                        mock_termios.tcsetattr.assert_called_once()
                        new_attr = mock_termios.tcsetattr.call_args[0][2]
                        self.assertEqual(new_attr[3] & mock_termios.ECHO, 0)
                    self.assertEqual(mock_termios.tcsetattr.call_args[0][2], orig_attr)

                # 2. Exception path
                mock_termios.tcsetattr.reset_mock()
                with mock.patch("sys.stdin.fileno", return_value=0):
                    with self.assertRaises(RuntimeError):
                        with agy_helper._temporary_no_echo_stdin():
                            raise RuntimeError("simulated error")
                    self.assertEqual(mock_termios.tcsetattr.call_args[0][2], orig_attr)

    def test_tty_echo_control_restored_on_parse_failure_and_graceful_on_switch_failure(self) -> None:
        from scripts import agy_helper

        mock_kernel32 = mock.MagicMock()
        mock_kernel32.GetStdHandle.return_value = 12345

        def fake_get_console_mode(handle, byref_mode):
            byref_mode._obj.value = 0x0007
            return 1

        mock_kernel32.GetConsoleMode.side_effect = fake_get_console_mode
        mock_kernel32.SetConsoleMode.return_value = 1

        with mock.patch("os.name", "nt"):
            with mock.patch("ctypes.windll.kernel32", mock_kernel32, create=True):
                # Parse failure path
                args = type("RequestArgs", (), {"request_stdin": True, "request_file": None})()
                with mock.patch("sys.stdin.buffer.readline", return_value=b"invalid-not-json\n"):
                    with self.assertRaises(agy_helper.HelperError) as ctx:
                        agy_helper._read_request(args)
                    self.assertEqual(ctx.exception.code, agy_helper.EXIT_REQUEST_INVALID)
                    # Mode must be restored to 0x0007
                    mock_kernel32.SetConsoleMode.assert_called_with(12345, 0x0007)

                # Graceful when GetConsoleMode fails (non-TTY)
                mock_kernel32.GetConsoleMode.side_effect = None
                mock_kernel32.GetConsoleMode.return_value = 0
                mock_kernel32.SetConsoleMode.reset_mock()
                with mock.patch(
                    "sys.stdin.buffer.readline",
                    return_value=b'{"workspace": "E:/w", "goal": "g", "scope": ["s"], "acceptance": ["a"]}\n',
                ):
                    req = agy_helper._read_request(args)
                    self.assertEqual(req["goal"], "g")
                    mock_kernel32.SetConsoleMode.assert_not_called()

    def test_helper_heartbeat_emitted_during_silent_producer(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-heartbeat-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            environment = os.environ.copy()
            environment["FAKE_AGY_PRE_FINAL_SLEEP"] = "0.35"
            output = StringIO()
            with mock.patch.dict(os.environ, environment, clear=True):
                with mock.patch.object(agy_helper, "HELPER_HEARTBEAT_SECONDS", 0.08):
                    with redirect_stdout(output):
                        code, summary = agy_helper._dispatch(
                            _base_request(root),
                            "review-local",
                            agy=str(agy),
                            skip_preflight=True,
                            run_timeout=2,
                        )
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
            heartbeats = [e for e in events if e.get("event") == "heartbeat"]
            self.assertGreaterEqual(len(heartbeats), 1, "Helper must emit heartbeats during producer silence")
            for hb in heartbeats:
                self.assertEqual(hb["task_id"], summary["task_id"])
                self.assertIn("phase", hb)
                self.assertIn("elapsed_seconds", hb)
                self.assertIn("tools", hb)
                self.assertNotIn("SECRET", json.dumps(hb))
            self.assertEqual(events[-1]["event"], "run")

    def test_helper_heartbeat_independent_of_reducer_budget(self) -> None:
        from scripts import agy_helper

        with tempfile.TemporaryDirectory(prefix="agy-helper-budget-hb-") as raw:
            root = Path(raw) / "workspace"
            root.mkdir()
            fake_dir = Path(raw) / "fake"
            fake_dir.mkdir()
            agy = _make_fake_agy(fake_dir)
            environment = os.environ.copy()
            environment["FAKE_AGY_BURST_TOOLS"] = "25"
            environment["FAKE_AGY_PRE_FINAL_SLEEP"] = "0.35"
            output = StringIO()
            with mock.patch.dict(os.environ, environment, clear=True):
                with mock.patch.object(agy_helper, "HELPER_HEARTBEAT_SECONDS", 0.08):
                    with redirect_stdout(output):
                        code, summary = agy_helper._dispatch(
                            _base_request(root),
                            "review-local",
                            agy=str(agy),
                            skip_preflight=True,
                            run_timeout=2,
                        )
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
            heartbeats = [e for e in events if e.get("event") == "heartbeat"]
            self.assertGreaterEqual(len(heartbeats), 1, "Helper must emit heartbeat even when reducer budget is exhausted")
            last_hb = heartbeats[-1]
            self.assertEqual(last_hb["tools"].get("view_file"), 25)


if __name__ == "__main__":
    unittest.main()
