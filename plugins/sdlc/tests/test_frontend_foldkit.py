"""Foldkit frontend contract (lib/frontend_foldkit.py): detection by init, the five fe-* checkers, oxlint in the
ratchet, and the verify stamp's frontend block.

The fixture under tests/fixtures/foldkit is a small Foldkit app with two programs: `nodes` carries one planted
finding per checker (an untested Message tag, a text-only clickable div, a call to a missing endpoint, a Model
field named apiKey, DevTools shown in production) and `settings` is clean. Plugin only: the kit has no frontend.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FORM, PACK

pytestmark = pytest.mark.skipif(FORM != "plugin", reason="the frontend checkers ship with the plugin only")

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "foldkit"
LIB = PACK / "lib" / "frontend_foldkit.py"
CHECKERS = ["fe_story", "fe_scene", "fe_api", "fe_secret", "fe_devtools"]
FRONTEND = {"framework": "foldkit", "programs": ["src/programs/**"], "api_table": "src/lib/api/endpoints.ts", "oxlint": False}
PLANTED = {
    "fe_story": ("fe-story|src/programs/nodes/message.ts|ClickedArchive", "src/programs/nodes/message.ts:13:"),
    "fe_scene": ("fe-scene|src/programs/nodes/view.ts|div:text(Archive)", "src/programs/nodes/view.ts:26:"),
    "fe_api": ("fe-api|POST /api/nodes/archive", "src/programs/nodes/commands.ts:31:"),
    "fe_secret": ("fe-secret|src/programs/nodes/model.ts|apiKey", "src/programs/nodes/model.ts:12:"),
    "fe_devtools": ("fe-devtools|src/main.ts", "src/main.ts:16:"),
}
GUARDED_MAIN = """\
import { Runtime } from 'foldkit'

import { Message } from './programs/settings/message'

const application = Runtime.makeApplication({
  devTools: import.meta.env.DEV ? { show: 'Always', Message } : false,
})
Runtime.run(application)
"""


class App:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.home = path.parent / "home"
        self.home.mkdir(exist_ok=True)

    def env(self, **extra: str) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("CLAUDE_", "GIT_", "VERIFY_", "DATABASE_URL", "REDIS_URL", "FOLDKIT_")) and k not in ("CI", "GITHUB_ACTIONS")}
        e.update(HOME=str(self.home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Test",
                 GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com",
                 CLAUDE_PLUGIN_ROOT=str(PACK), CLAUDE_PROJECT_DIR=str(self.path), PYTHONDONTWRITEBYTECODE="1")
        e.update(extra)
        return e

    def run(self, script: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(script), *args], cwd=self.path, capture_output=True,
                              text=True, env=env or self.env(), timeout=300)

    def write(self, rel: str, text: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def read(self, rel: str) -> str:
        return (self.path / rel).read_text(encoding="utf-8")

    def config(self, **fields) -> None:
        cfg = {"version": 1, "stack": "node", "checkers": list(CHECKERS), "frontend": dict(FRONTEND),
               "commands": {"test": "true", "lint_report": "", "lint_fix": "", "format": "", "typecheck": ""}}
        cfg.update(fields)
        self.write(".claude/sdlc.json", json.dumps(cfg, indent=1) + "\n")

    def git(self, *args: str) -> str:
        p = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, env=self.env())
        assert p.returncode == 0, p.stderr
        return p.stdout

    def commit(self, msg: str = "chore: snapshot") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def check(self, name: str) -> tuple[int, list[tuple[str, str]]]:
        p = self.run(LIB, "--check", name, "--root", str(self.path))
        assert p.returncode in (0, 1), p.stderr
        return p.returncode, [tuple(line.split("\t", 1)) for line in p.stdout.splitlines() if "\t" in line]

    def keys(self, name: str) -> set[str]:
        return {k for k, _ in self.check(name)[1]}


@pytest.fixture
def app(tmp_path) -> App:
    path = tmp_path / "app"
    shutil.copytree(FIXTURE, path)
    a = App(path)
    a.git("init", "-q", "-b", "main")
    a.config()
    return a


# ---------- the planted fixture ----------

@pytest.mark.parametrize("name", CHECKERS)
def test_each_checker_reports_exactly_its_planted_finding_with_file_and_line(app, name):
    rc, rows = app.check(name)
    key, where = PLANTED[name]
    assert rc == 1
    assert [k for k, _ in rows] == [key]
    assert rows[0][1].startswith(where), rows[0][1]


def test_a_clean_program_yields_no_finding_from_any_checker(app):
    app.config(frontend={**FRONTEND, "programs": ["src/programs/settings/**"]})
    app.write("src/main.ts", GUARDED_MAIN)
    for name in CHECKERS:
        assert app.check(name) == (0, []), name


def test_the_template_folder_and_comments_are_not_read_as_program_code(app):
    # _template/message.ts declares an untested tag; the nodes story mentions ClickedArchive(...) in a comment only
    assert app.keys("fe_story") == {PLANTED["fe_story"][0]}
    assert "ClickedArchive(" in app.read("src/programs/nodes/nodes.story.test.ts")


def test_summary_is_the_stamp_block(app):
    p = app.run(LIB, "--summary", "--root", str(app.path))
    assert json.loads(p.stdout) == {"framework": "foldkit", "programs": 2, "stories": {"tags": 10, "covered": 9},
                                    "scenes": {"files": 2, "with_exist": 2}, "api": {"calls": 5, "matched": 4},
                                    "oxlint": {"findings": None}}


def test_a_repo_without_a_frontend_block_runs_no_frontend_checker(app):
    app.config(frontend={})
    for name in CHECKERS:
        assert app.check(name) == (0, [])


# ---------- formatting and rule edges ----------

def test_story_union_parsing_tolerates_formatting_comments_braces_in_strings_and_regexes(app):
    app.write("src/programs/odd/message.ts", (
        "import { defineMessageUnion } from 'foldkit/message'\n"
        "const re = /[{(]/g // a regex with brackets {\n"
        "export const Message = defineMessageUnion({ClickedA:{},'ClickedB':{ label: Schema.String },\n"
        "  /* ClickedC: {}, */ ChangedText: { text: Schema.String } , })\n"
        "const s = '}) defineMessageUnion({ Fake: {} })'\n"))
    app.write("src/programs/odd/story.test.ts", (
        "Story.story(update, Story.message(Message.ClickedA()), Story.message(ChangedText ({ text: '}' })))\n"))
    assert {k for k in app.keys("fe_story") if "/odd/" in k} == {"fe-story|src/programs/odd/message.ts|ClickedB"}


def test_a_tag_is_covered_only_by_a_story_in_its_own_program(app):
    app.write("src/programs/settings/settings.story.test.ts",
              app.read("src/programs/settings/settings.story.test.ts") + "\n// a story elsewhere\nStory.message(Message.ClickedArchive({ id: 'x' }))\n")
    assert PLANTED["fe_story"][0] in app.keys("fe_story")


def test_scene_flags_unlabelled_fields_empty_buttons_and_hrefless_links_and_accepts_labels(app):
    app.write("src/programs/form/view.ts", (
        "import type { HtmlBuilder } from 'foldkit/html'\n"
        "export const view = (model, h: HtmlBuilder<Message>) => h.div([], [\n"
        "  h.input([h.OnInput(v => Message.ChangedQuery({ v }))]),\n"
        "  h.button([h.OnClick(Message.ClickedClose())], []),\n"
        "  h.a([h.OnClick(Message.ClickedMore())], ['More']),\n"
        "  h.label([], ['Name', h.input([h.OnInput(v => Message.ChangedName({ v }))])]),\n"
        "  h.label([h.For('email')], ['Email']), h.input([h.Id('email')]),\n"
        "  h.a([h.Href('/docs')], ['Docs']),\n"
        "  h.span([h.Role('button'), h.AriaLabel('Close'), h.OnClick(Message.ClickedClose())], []),\n"
        "])\n"))
    app.write("src/programs/form/form.scene.test.ts", "Scene.scene({ update, view }, Scene.click(Scene.text('More')))\n")
    keys = {k for k in app.keys("fe_scene") if "/form/" in k}
    assert keys == {"fe-scene|src/programs/form/view.ts|input:on(ChangedQuery)",
                    "fe-scene|src/programs/form/view.ts|button:on(ClickedClose)",
                    "fe-scene|src/programs/form/view.ts|a:text(More)",
                    "fe-scene|src/programs/form/form.scene.test.ts|no-toExist"}


def test_api_matches_templates_and_origins_reports_templated_misses_with_a_tilde_and_ignores_navigation(app):
    app.write("src/programs/runs/commands.ts", (
        "import { Command } from 'foldkit'\n"
        "export const A = Command.define('A', { execute: ({ id }) => fetch(`/api/runs/${id}/logs`) })\n"
        "export const B = Command.define('B', { execute: fetch('https://api.example.test/api/nodes?limit=5') })\n"
        "export const C = Command.define('C', { execute: Navigation.pushUrl('/runs') })\n"
        "export const D = Command.define('D', { execute: HttpClientRequest.del('/api/nodes/n1') })\n"))
    keys = {k for k in app.keys("fe_api") if k != PLANTED["fe_api"][0]}
    assert keys == {"fe-api|~GET /api/runs/{}/logs", "fe-api|DELETE /api/nodes/n1"}


def test_api_without_the_table_says_so(app):
    (app.path / "src/lib/api/endpoints.ts").unlink()
    rows = app.check("fe_api")[1]
    assert rows and all("does not exist" in m for _, m in rows)


def test_secret_is_camel_case_aware_and_skips_redacted_benign_and_marked_fields(app):
    app.write("src/programs/auth/model.ts", (
        "import { Schema } from 'effect'\n"
        "export const Model = Schema.Struct({\n"
        "  accessToken: Schema.String, client_secret: Schema.String, 'refresh-token': Schema.String,\n"
        "  session: Schema.Struct({ password: Schema.String }),\n"
        "  maxTokens: Schema.Number, isPasswordVisible: Schema.Boolean, secretName: Schema.String,\n"
        "  csrfToken: Schema.String, // secret: ok rotated per request, not a credential\n"
        "  idToken: Schema.Redacted(Schema.String),\n"
        "})\n"))
    keys = {k for k in app.keys("fe_secret") if "/auth/" in k}
    assert keys == {f"fe-secret|src/programs/auth/model.ts|{f}" for f in ("accessToken", "client_secret", "refresh-token", "password")}


@pytest.mark.parametrize("text,flagged", [
    ("Runtime.makeApplication({ devTools: { show: 'Always' } })", True),
    ("Runtime.makeApplication({ devTools: process.env.NODE_ENV === 'production' ? { show: 'Always' } : false })", True),
    ("Runtime.makeApplication({ devTools: import.meta.env.DEV ? { show: 'Always' } : false })", False),
    ("Runtime.makeApplication({ devTools: { show: import.meta.env.DEV ? 'Always' : 'Development' } })", False),
    ("if (import.meta.env.DEV) {\n  config.devTools = { show: 'Always' }\n}", False),
    ("const devTools = process.env.NODE_ENV !== 'production' ? { show: 'Always' } : false", False),
    ("export default defineConfig({ plugins: [foldkit({ devToolsMcpPort: 9988 })] })", True),
    ("tooltip({ show: 'Always' })", False),
    ("const doc = \"devTools: { show: 'Always' }\"", False),
])
def test_devtools_exposure_needs_a_development_check(app, text, flagged):
    app.write("src/main.ts", "import { Runtime } from 'foldkit'\n" + text + "\n")
    assert ("fe-devtools|src/main.ts" in app.keys("fe_devtools")) is flagged


# ---------- detection and init ----------

def _detect(path: Path) -> dict | None:
    p = subprocess.run([sys.executable, "-I", str(LIB), "--detect", "--root", str(path)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(path)})
    return json.loads(p.stdout)


def test_detect_reads_the_root_package_and_workspace_packages(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    assert _detect(tmp_path) is None
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
    (tmp_path / "apps" / "console").mkdir(parents=True)
    (tmp_path / "apps" / "console" / "package.json").write_text(json.dumps({"devDependencies": {"foldkit": "0.170.0"}}))
    assert _detect(tmp_path) == {"framework": "foldkit", "programs": ["apps/console/src/programs/**"],
                                 "api_table": "apps/console/src/lib/api/endpoints.ts", "oxlint": True}
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"foldkit": "0.170.0"}}))
    assert _detect(tmp_path)["programs"] == ["src/programs/**"]


def test_init_writes_the_frontend_block_and_checkers_and_says_so(app):
    (app.path / ".claude" / "sdlc.json").unlink()
    app.commit("chore: app")
    init = PACK / "scripts" / "init_project.py"
    dry = app.run(init, "--root", str(app.path), "--dry-run", "--no-ci")
    assert dry.returncode == 0, dry.stderr
    assert "frontend: foldkit" in dry.stdout and not (app.path / ".claude" / "sdlc.json").exists()
    real = app.run(init, "--root", str(app.path), "--no-ci", "--allow-partial-baseline")
    assert real.returncode == 0, real.stdout + real.stderr
    assert "frontend: foldkit" in real.stdout
    cfg = json.loads(app.read(".claude/sdlc.json"))
    assert cfg["frontend"] == {**FRONTEND, "oxlint": True}
    assert cfg["checkers"][-5:] == CHECKERS
    baseline = json.loads(app.read(".claude/baseline.json"))["counts"]
    assert {key for key, _ in PLANTED.values()} <= set(baseline)


def test_init_upgrade_never_adds_the_frontend_block(app):
    app.config(frontend=None, checkers=["module_state"])
    cfg = json.loads(app.read(".claude/sdlc.json"))
    cfg.pop("frontend")
    app.write(".claude/sdlc.json", json.dumps(cfg))
    app.commit("chore: app")
    p = app.run(PACK / "scripts" / "init_project.py", "--root", str(app.path), "--upgrade", "--no-ci")
    assert "frontend: foldkit detected, not added by --upgrade" in p.stdout, p.stdout + p.stderr
    assert "frontend" not in json.loads(app.read(".claude/sdlc.json"))


def test_init_reports_the_kept_config_not_the_one_it_would_have_written(app):
    app.config(frontend=None, checkers=["module_state"])
    cfg = json.loads(app.read(".claude/sdlc.json"))
    cfg.pop("frontend")
    app.write(".claude/sdlc.json", json.dumps(cfg))
    app.commit("chore: app")
    before = app.read(".claude/sdlc.json")
    init = PACK / "scripts" / "init_project.py"
    for extra in (["--dry-run"], ["--allow-partial-baseline"]):
        p = app.run(init, "--root", str(app.path), "--no-ci", *extra)
        out = p.stdout + p.stderr
        assert p.returncode == 0, out
        assert "= kept  .claude/sdlc.json (yours; never replaced)" in p.stdout, out
        assert "frontend: foldkit detected, not added (.claude/sdlc.json exists and was kept)" in p.stdout, out
        assert "frontend: foldkit  (" not in p.stdout, out
        assert app.read(".claude/sdlc.json") == before
    # A kept config that already has the block is reported as it is on disk.
    app.config(checkers=["module_state", "fe_story"])
    p = app.run(init, "--root", str(app.path), "--no-ci", "--dry-run")
    assert "frontend: foldkit  (" in p.stdout and "checkers fe_story)" in p.stdout, p.stdout + p.stderr


# ---------- ratchet and verify ----------

def _fake_oxlint(tmp: Path, payload: dict) -> str:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "oxlint"
    exe.write_text(f"#!{sys.executable}\nimport sys\nprint({json.dumps(json.dumps(payload))})\nsys.exit(1)\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(bin_dir)


def _bare_path(tmp: Path) -> str:
    """A PATH with python and git but no oxlint."""
    keep = tmp / "barebin"
    keep.mkdir(exist_ok=True)
    for tool in ("git", "true"):
        found = shutil.which(tool)
        if found and not (keep / tool).exists():
            (keep / tool).symlink_to(found)
    return f"{keep}{os.pathsep}{os.path.dirname(sys.executable)}"


def test_ratchet_reports_fe_keys_as_new_then_known(app):
    app.commit("chore: app")
    ratchet = PACK / "scripts" / "ratchet.py"
    first = app.run(ratchet, "--root", str(app.path), "--all")
    assert first.returncode == 1
    for key, where in PLANTED.values():
        assert f"NEW    {where}" in first.stdout, key
    assert app.run(ratchet, "--root", str(app.path), "--update").returncode == 0
    assert app.run(ratchet, "--root", str(app.path)).returncode == 0
    app.write("src/programs/settings/message.ts",
              app.read("src/programs/settings/message.ts").replace("ClickedSave: {},", "ClickedSave: {},\n  ClickedReset: {},"))
    again = app.run(ratchet, "--root", str(app.path))
    assert again.returncode == 1 and "Message tag ClickedReset" in again.stdout


def test_ratchet_runs_oxlint_with_the_foldkit_plugin_and_keys_rules_like_eslint(app, tmp_path):
    app.config(frontend={**FRONTEND, "oxlint": True})
    payload = {"diagnostics": [{"message": "Module-level let/var holds state outside the Model.",
                                "code": "foldkit(no-module-level-mutable-state)", "severity": "error",
                                "filename": "src/programs/nodes/update.ts", "labels": [{"span": {"offset": 0, "length": 3, "line": 4, "column": 1}}]}],
               "number_of_files": 1, "number_of_rules": 37}
    env = app.env(PATH=_fake_oxlint(tmp_path, payload) + os.pathsep + _bare_path(tmp_path))
    p = app.run(PACK / "scripts" / "ratchet.py", "--root", str(app.path), "--all", env=env)
    assert "NEW    src/programs/nodes/update.ts:4: foldkit/no-module-level-mutable-state Module-level" in p.stdout, p.stdout + p.stderr


def test_ratchet_skips_oxlint_with_a_note_when_it_is_missing_and_keeps_its_keys(app, tmp_path):
    app.config(frontend={**FRONTEND, "oxlint": True})
    app.commit("chore: app")
    env = app.env(PATH=_bare_path(tmp_path))
    ratchet = PACK / "scripts" / "ratchet.py"
    assert app.run(ratchet, "--root", str(app.path), "--update", env=env).returncode == 0
    base = json.loads(app.read(".claude/baseline.json"))
    base["counts"]["lint|src/programs/nodes/view.ts|foldkit/keyed-required-for-mapped-rows"] = 1
    app.write(".claude/baseline.json", json.dumps(base))
    p = app.run(ratchet, "--root", str(app.path), env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "oxlint is not installed" in p.stderr and "Foldkit lint rules not ratcheted" in p.stderr
    assert "stale" not in p.stdout
    ci = app.run(ratchet, "--root", str(app.path), "--require-tools", env=env)
    assert ci.returncode == 2 and "lint|src/programs/" in ci.stderr


def test_verify_stamps_the_frontend_block_and_fails_only_on_new_story_or_scene_keys(app):
    app.commit("chore: app")
    ratchet = PACK / "scripts" / "ratchet.py"
    assert app.run(ratchet, "--root", str(app.path), "--update").returncode == 0
    app.write(".gitignore", ".claude/work/last_verify.json\n")
    app.commit("chore: baseline")
    green = app.run(PACK / "scripts" / "verify.py", "--root", str(app.path))
    assert green.returncode == 0, green.stdout[-2000:]
    assert "== frontend\nstories: 9/10 Message tags sent by a story test" in green.stdout
    stamp = json.loads(app.read(".claude/work/last_verify.json"))
    assert stamp["checks"]["frontend"] == 0
    assert stamp["frontend"] == {"framework": "foldkit", "programs": 2, "stories": {"tags": 10, "covered": 9},
                                 "scenes": {"files": 2, "with_exist": 2}, "api": {"calls": 5, "matched": 4},
                                 "oxlint": {"findings": None}}
    app.write("src/programs/settings/view.ts", app.read("src/programs/settings/view.ts").replace(
        "button([OnClick(Message.ClickedSave()), Disabled(model.isSaving)], ['Save']),",
        "button([OnClick(Message.ClickedSave()), Disabled(model.isSaving)], ['Save']),\n      form([OnClick(Message.ClickedSave())], ['Save now']),"))
    red = app.run(PACK / "scripts" / "verify.py", "--root", str(app.path))
    assert red.returncode == 1 and "FAILED: frontend" in red.stdout
    assert "NEW src/programs/settings/view.ts:" in red.stdout and "text('Save now')" in red.stdout
    assert not (app.path / ".claude" / "work" / "last_verify.json").exists()
