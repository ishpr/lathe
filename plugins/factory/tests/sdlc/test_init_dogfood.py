"""Init findings from the lathe dogfood: a monorepo with no marker at the root, project settings, worktrees, gitignore,
PR-size excludes, a sandbox-refused baseline, version-proof plugin rules."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[2]
INIT = PACK / "scripts" / "init_project.py"


def _mod():
    spec = importlib.util.spec_from_file_location("init_project_dogfood", INIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(root: Path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)


def _monorepo(tmp_path: Path) -> Path:
    """No marker at the root; pyproject.toml two levels down in two projects (the lathe layout)."""
    root = tmp_path / "mono"
    fac = root / "plugins" / "factory"
    (fac / "sdlc_graph").mkdir(parents=True)
    (fac / "sdlc_graph" / "__init__.py").write_text("")
    (fac / "tests").mkdir()
    (fac / "pyproject.toml").write_text("[project]\nname='f'\n[tool.uv]\n")
    app = root / "examples" / "sample-app"
    (app / "app").mkdir(parents=True)
    (app / "app" / "__init__.py").write_text("")
    (app / "app" / "main.py").write_text("")
    (app / "pyproject.toml").write_text("[project]\nname='s'\n")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "package.json").write_text("{}")
    _git(root)
    return root


def _run(root: Path, *args: str, env_extra: dict | None = None):
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(PACK),
        "SDLC_INIT_NO_MIGRATE": "1",
        **(env_extra or {}),
    }
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(INIT),
            "--root",
            str(root),
            "--allow-partial-baseline",
            "--no-ci",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


# 1, 2: stack markers one level down, paths from the projects ---------------------------------------------------------


def test_find_projects_sees_markers_below_the_root_and_skips_dependency_dirs(tmp_path):
    root = _monorepo(tmp_path)
    assert _mod().find_projects(str(root)) == {
        "python": ["examples/sample-app", "plugins/factory"]
    }


def test_init_on_a_monorepo_without_a_root_marker_derives_paths_from_the_projects(
    tmp_path,
):
    root = _monorepo(tmp_path)
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = json.loads((root / ".claude" / "sdlc.json").read_text())
    assert cfg["stack"] == "python"
    assert cfg["projects"] == ["examples/sample-app", "plugins/factory"]
    assert cfg["paths"]["app"] == [
        "examples/sample-app/app/**",
        "plugins/factory/sdlc_graph/**",
    ]
    assert cfg["paths"]["tests"] == [
        "examples/sample-app/tests/**",
        "plugins/factory/tests/**",
    ]
    assert cfg["entry_modules"] == ["examples/sample-app/app/main.py"]
    assert cfg["commands"]["setup"] == ""
    assert "projects: examples/sample-app, plugins/factory" in r.stdout


def test_project_flag_narrows_to_one_project(tmp_path):
    root = _monorepo(tmp_path)
    r = _run(root, "--project", "plugins/factory")
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = json.loads((root / ".claude" / "sdlc.json").read_text())
    assert cfg["projects"] == ["plugins/factory"]
    assert cfg["commands"]["test"] == "uv run --project plugins/factory pytest -q"
    assert _run(root, "--project", "nope").returncode == 2


def test_default_paths_that_match_nothing_are_named(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    _git(root)
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "match nothing yet" in r.stdout and "app/**" in r.stdout


# 35, 10: project settings --------------------------------------------------------------------------------------------


def test_settings_register_the_plugin_and_trim_the_allow_list_to_the_stack(tmp_path):
    root = _monorepo(tmp_path)
    assert _run(root, "--project", "plugins/factory").returncode == 0
    st = json.loads((root / ".claude" / "settings.json").read_text())
    key = next(iter(st["enabledPlugins"]))
    assert st["enabledPlugins"][key] is True and key.startswith("factory@")
    assert st["extraKnownMarketplaces"][key.split("@")[1]]["source"] == {
        "source": "github",
        "repo": "ishpr/lathe",
    }
    allow = st["permissions"]["allow"]
    assert "Bash(uv run --project plugins/factory pytest *)" in allow
    assert (
        "Bash(uv run --project plugins/factory pytest -q)" in allow
    )  # commands.test exactly
    for other in (
        "Bash(poetry run pytest *)",
        "Bash(pnpm test *)",
        "Bash(cargo test *)",
        "Bash(go test *)",
        "Bash(npm ci)",
        "Bash(poetry install)",
        "Bash(uv sync --frozen)",
    ):
        assert other not in allow, other
    assert "Bash(make test)" in allow  # stack-neutral rules stay


def test_node_repo_keeps_its_own_runner_and_drops_the_others(tmp_path):
    root = tmp_path / "n"
    root.mkdir()
    (root / "package.json").write_text('{"name":"n","scripts":{"test":"vitest run"}}')
    (root / "pnpm-lock.yaml").write_text("")
    _git(root)
    assert _run(root).returncode == 0
    allow = json.loads((root / ".claude" / "settings.json").read_text())["permissions"][
        "allow"
    ]
    assert any(r.startswith("Bash(pnpm ") for r in allow)
    assert "Bash(pnpm install --frozen-lockfile)" in allow
    assert not any(
        r.startswith(("Bash(uv ", "Bash(poetry ", "Bash(cargo ", "Bash(yarn "))
        for r in allow
    )


def test_version_wildcard_survives_a_plugin_update():
    mod = _mod()
    assert (
        mod.version_wildcard("/h/plugins/cache/lathe/factory/0.6.0")
        == "/h/plugins/cache/lathe/factory/*"
    )
    assert (
        mod.version_wildcard("/h/plugins/cache/lathe/factory/0.7.1-rc1")
        == "/h/plugins/cache/lathe/factory/*"
    )
    assert (
        mod.version_wildcard("/src/lathe/plugins/factory")
        == "/src/lathe/plugins/factory"
    )
    assert mod.is_machine_rule(
        'Bash(python3 -I "/h/plugins/cache/lathe/factory/*/scripts/*)'
    )


def test_machine_rules_for_a_versioned_cache_name_no_version(tmp_path):
    root = _monorepo(tmp_path)
    cache = tmp_path / "cache" / "lathe" / "factory" / "0.6.0"
    cache.parent.mkdir(parents=True)
    cache.symlink_to(PACK)
    r = _run(root, env_extra={"CLAUDE_PLUGIN_ROOT": str(cache)})
    assert r.returncode == 0, r.stdout + r.stderr
    local = json.loads((root / ".claude" / "settings.local.json").read_text())[
        "permissions"
    ]["allow"]
    assert f'Bash(python3 -I "{tmp_path}/cache/lathe/factory/*/scripts/*)' in local
    assert not any("0.6.0" in rule for rule in local)


# 3, 36: worktrees ----------------------------------------------------------------------------------------------------


def test_existing_worktrees_outside_the_checkout_join_allow_write(tmp_path):
    root = _monorepo(tmp_path)
    for cmd in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"],
    ):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)
    sibling = tmp_path / "sibling"
    inside = root / ".claude" / "worktrees" / "inner"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", str(sibling), "-b", "s"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", str(inside), "-b", "i"],
        check=True,
    )
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    local = json.loads((root / ".claude" / "settings.local.json").read_text())
    assert local["sandbox"]["filesystem"]["allowWrite"] == [
        str(sibling)
    ]  # the one under the checkout is already covered
    assert str(sibling) in r.stdout
    assert "agent cannot create a checkout under the sandbox" in r.stdout
    assert "git worktree add .claude/worktrees/<name> -b <branch>" in r.stdout


# 27: gitignore -------------------------------------------------------------------------------------------------------


def test_gitignore_ignores_the_work_directory_except_its_readme(tmp_path):
    root = _monorepo(tmp_path)
    assert _run(root).returncode == 0
    lines = (root / ".gitignore").read_text().splitlines()
    assert lines.index(".claude/work/*") < lines.index("!.claude/work/README.md")
    work = root / ".claude" / "work"
    (work / "handoffs.md").write_text("x")
    (work / "spawns.json").write_text("{}")
    ignored = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "check-ignore",
            ".claude/work/handoffs.md",
            ".claude/work/spawns.json",
            ".claude/work/README.md",
        ],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert ignored == [".claude/work/handoffs.md", ".claude/work/spawns.json"]


# 28: PR size ---------------------------------------------------------------------------------------------------------


def test_pr_size_excludes_the_files_init_generates(tmp_path):
    root = _monorepo(tmp_path)
    assert _run(root).returncode == 0
    size = json.loads((root / ".claude" / "sdlc.json").read_text())["pr_size"]
    assert (
        ".claude/baseline.json" in size["exclude"]
        and "docs/reviews/**" in size["exclude"]
    )
    assert "*.lock" in size["exclude"] and size["fail"] == 1000
    sys.path.insert(0, str(PACK / "scripts"))
    import check_pr_size

    assert check_pr_size.excluded(".claude/baseline.json", size["exclude"])
    assert check_pr_size.excluded("docs/reviews/2026-11/pack.html", size["exclude"])


def test_upgrade_adds_the_excludes_to_an_existing_pr_size(tmp_path):
    root = _monorepo(tmp_path)
    assert _run(root).returncode == 0
    cfg_path = root / ".claude" / "sdlc.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["pr_size"]["exclude"] = ["*.lock"]
    cfg_path.write_text(json.dumps(cfg))
    assert _run(root, "--upgrade").returncode == 0
    assert json.loads(cfg_path.read_text())["pr_size"]["exclude"] == [
        "*.lock",
        ".claude/baseline.json",
        "docs/reviews/**",
    ]


# 12: baseline refused by the sandbox ---------------------------------------------------------------------------------


def test_baseline_refused_by_the_sandbox_prints_the_human_command(
    tmp_path, monkeypatch, capsys
):
    mod = _mod()
    root = _monorepo(tmp_path)
    real = mod.cl.run

    def fake(cmd, *a, **kw):
        if any(str(c).endswith("ratchet.py") for c in cmd):
            return subprocess.CompletedProcess(
                cmd,
                1,
                "",
                "Traceback (most recent call last):\n  File ratchet.py\nPermissionError: [Errno 1] Operation not permitted: 'x'\n",
            )
        return real(cmd, *a, **kw)

    monkeypatch.setattr(mod.cl, "run", fake)
    rc = mod.main(["--root", str(root), "--no-ci", "--allow-partial-baseline"])
    out = capsys.readouterr()
    assert rc == 0
    assert "Traceback" not in out.out + out.err
    assert "baseline: not recorded" in out.out
    assert "ratchet.py" in out.out and "--update" in out.out and "python3 -I" in out.out
