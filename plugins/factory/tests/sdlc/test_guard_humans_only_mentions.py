"""Finding #54: running a checkout of the plugin's own init from the plugin's repo tripped the humans-only scan, because
init only *prints* the human's `ratchet.py --update` command. Only a byte-identical copy of the installed plugin's
script is trusted; any other script naming a humans-only command with its flag is still refused, however it is split."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).resolve().parents[2] / "hooks" / "guard_bash.py"


def _mod():
    spec = importlib.util.spec_from_file_location("guard_bash_mentions", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_byte_identical_copy_of_the_plugin_script_is_trusted(tmp_path):
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    body = 'print("a human runs: python3 -I scripts/ratchet.py --update")\n'
    (root / "scripts" / "init_project.py").write_text(body)
    copy = tmp_path / "repo" / "init_project.py"
    copy.parent.mkdir()
    copy.write_text(body)
    assert _mod().same_as_plugin_copy(str(copy), str(root)) is True


def test_a_modified_copy_a_missing_twin_and_the_plugin_file_itself_are_not_trusted(
    tmp_path,
):
    # negative cases: only an exact copy of reviewed plugin code is exempt
    mod = _mod()
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "init_project.py").write_text("print('x')\n")
    edited = tmp_path / "init_project.py"
    edited.write_text(
        "print('x')\nimport subprocess; subprocess.run(['ratchet.py', '--update'])\n"
    )
    assert not mod.same_as_plugin_copy(str(edited), str(root))
    other = tmp_path / "helper.py"
    other.write_text("print('x')\n")
    assert not mod.same_as_plugin_copy(str(other), str(root))
    assert (
        mod.same_as_plugin_copy(str(root / "scripts" / "init_project.py"), str(root))
        is False
    )
    assert mod.same_as_plugin_copy(str(edited), "") is False


def test_a_script_that_builds_the_command_over_two_lines_is_still_refused(tmp_path):
    # negative case: splitting the name and the flag across lines does not slip past the scan
    script = tmp_path / "sneaky.py"
    script.write_text(
        'cmd = ["python3", "scripts/ratchet.py"]\ncmd.append("--update")\nimport subprocess; subprocess.run(cmd)\n'
    )
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"python3 {script}"},
        "cwd": str(tmp_path),
    }
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)},
        check=False,
    )
    out = (r.stdout + r.stderr).lower()
    assert r.returncode == 2 or '"deny"' in out or '"ask"' in out, out
