from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


PIPELINE_ROOT = Path(__file__).resolve().parents[2]


def test_standalone_timing_prints_step_result() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.pipeline.standalone_timing import start_standalone_timer; start_standalone_timer()",
        ],
        check=True,
        capture_output=True,
        cwd=PIPELINE_ROOT,
        text=True,
    )

    assert re.search(r"PASO \[OK\] finalizado en \d+\.\d{2}s", completed.stdout)


def test_standalone_timing_is_suppressed_inside_pipeline(runtime_dir) -> None:
    env = os.environ.copy()
    env["SOCCERDATA_TASK_RESULT_PATH"] = str(runtime_dir / "task_result.json")
    env["SOCCERDATA_PIPELINE_MANAGED_STEP"] = "1"

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.pipeline.standalone_timing import start_standalone_timer; start_standalone_timer()",
        ],
        check=True,
        capture_output=True,
        cwd=PIPELINE_ROOT,
        text=True,
        env=env,
    )

    assert completed.stdout == ""


def test_task_result_path_alone_does_not_create_standalone_report(runtime_dir) -> None:
    result_path = runtime_dir / "task_result.json"
    env = os.environ.copy()
    env["SOCCERDATA_TASK_RESULT_PATH"] = str(result_path)
    env.pop("SOCCERDATA_PIPELINE_MANAGED_STEP", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from src.pipeline.standalone_timing import start_standalone_timer; "
                "start_standalone_timer()"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=PIPELINE_ROOT,
        text=True,
        env=env,
    )

    assert re.search(r"PASO \[OK\] finalizado en \d+\.\d{2}s", completed.stdout)
    assert not result_path.exists()


def test_script_result_path_alone_does_not_create_standalone_report(runtime_dir) -> None:
    result_path = runtime_dir / "task_result.json"
    env = os.environ.copy()
    env["SOCCERDATA_TASK_RESULT_PATH"] = str(result_path)
    env.pop("SOCCERDATA_PIPELINE_MANAGED_STEP", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from src.pipeline.script_result import run_with_optional_task_result; "
                "run_with_optional_task_result('task', 'phase', lambda: {})"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=PIPELINE_ROOT,
        text=True,
        env=env,
    )

    assert re.search(r"PASO \[OK\] finalizado en \d+\.\d{2}s", completed.stdout)
    assert not result_path.exists()
