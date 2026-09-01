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
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
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


def _make_fake_agy(folder: Path) -> Path:
    script = folder / "fake_agy.py"
    script.write_text(textwrap.dedent(FAKE_AGY), encoding="utf-8")
    return script


def _base_request(workspace: Path) -> dict:
    return {
        "workspace": str(workspace),
        "goal": "审查登录流程并给出证据",
        "scope": ["src/**", "tests/**"],
        "acceptance": ["每项发现包含文件和复现证据"],
    }


class HelperContractTests(unittest.TestCase):
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
