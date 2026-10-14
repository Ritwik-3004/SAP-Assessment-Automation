"""Runs the standalone suites in tests/suites/ (scoring, retry endpoints, streaming chat, chat data policy) as part of
the normal test run. Each is a script that prints PASS/FAIL lines and a final "N/N passed"; it is run in its own
process because it replaces parts of the app (fake model, fake SAP, fake clock) at import time."""

import pathlib
import re
import subprocess
import sys

SUITES = pathlib.Path(__file__).resolve().parent / "suites"
BACKEND = SUITES.parents[1]


def run_suite(name: str, timeout: int = 600) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, str(SUITES / name)], cwd=str(BACKEND), capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return done.returncode, done.stdout + done.stderr


def assert_all_passed(name: str) -> None:
    code, output = run_suite(name)
    failed = [line for line in output.splitlines() if line.startswith("FAIL")]
    summary = re.findall(r"(\d+)/(\d+) passed", output)
    assert summary, f"{name} printed no summary:\n{output[-1500:]}"
    passed, total = map(int, summary[-1])
    assert not failed and passed == total, f"{name}: {passed}/{total} passed; failed: {failed[:5]}"


def test_scoring_suite():
    assert_all_passed("scoring_suite.py")


def test_retry_endpoints_suite():
    assert_all_passed("retry_suite.py")


def test_streaming_chat_suite():
    assert_all_passed("streaming_suite.py")


def test_chat_data_policy_suite():
    code, output = run_suite("chat_policy_suite.py")
    assert code == 0 and "Traceback" not in output, output[-1500:]
    assert "False" not in output, [line for line in output.splitlines() if "False" in line]
