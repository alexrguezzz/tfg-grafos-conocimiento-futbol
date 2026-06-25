from __future__ import annotations

from pathlib import Path
import shutil
import sys
import uuid

import pytest


PIPELINE_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PIPELINE_ROOT / "src"

for path in (
    PIPELINE_ROOT,
    SRC_DIR,
    SRC_DIR / "pipeline",
    SRC_DIR / "rdf",
    SRC_DIR / "validation",
):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.append(path_text)


@pytest.fixture
def runtime_dir() -> Path:
    runtime_root = PIPELINE_ROOT / "tests" / "runtime"
    root = runtime_root / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            runtime_root.rmdir()
        except OSError:
            pass
