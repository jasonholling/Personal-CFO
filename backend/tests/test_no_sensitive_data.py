"""
Runs the same regression check as scripts/check_sensitive_data.py, but via
pytest so it shows up in the normal local test run too, not just CI.
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_sensitive_data.py"


def test_no_known_sensitive_strings_in_tracked_files():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
