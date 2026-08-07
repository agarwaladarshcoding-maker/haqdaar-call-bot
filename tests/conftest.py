"""Shared pytest fixtures. Tests run against a fresh copy of the 20-scheme
demo DB (scripts/seed_demo.py), never the real 100-scheme catalogue, so
narrowing behaviour is asserted against known, controlled rules."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


@pytest.fixture()
def demo_db(tmp_path):
    db_path = str(tmp_path / "test_haqdaar.db")
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "seed_demo.py"), db_path],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return db_path
