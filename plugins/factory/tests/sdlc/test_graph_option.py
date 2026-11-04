"""The graph option: the `graph` block of .claude/sdlc.json, `/factory:graph` on/off/url/status, and the neon, aws and
spanner provisioning procedures run against fake CLIs.

The fakes (`neonctl`, `aws`, `gcloud`, and `uv` standing in for `uv run --project <plugin> sdlc-graph ...`) are one
Python shim written to a bin directory put first on PATH. Each call appends {"prog", "argv"} to a log; a state
directory plays the cloud (what exists), and every answer is canned JSON. No real cloud resource is created and no
network is used. Local provisioning runs for real in tests/graph/test_graph_option_ledger.py (Docker and Postgres).

Secrets in these tests are built at runtime, so no key-shaped literal is in the file.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PACK

sys.path.insert(0, str(PACK / "lib"))
import sdlc as cl  # noqa: E402

GRAPH_PY = PACK / "scripts" / "graph.py"
INIT_PY = PACK / "scripts" / "init_project.py"
NEON_HOST = "ep-quiet-sky-0001.us-east-2.aws.neon.tech"
RDS_HOST = "factory-graph-proj.c0ffee.us-east-1.rds.amazonaws.com"
AURORA_HOST = "factorygraphdb-graphdb.cluster-c0ffee.us-east-1.rds.amazonaws.com"
LEDGER_HOST = "ledger.example.test"

SHIM = r'''
import json, os, sys
from pathlib import Path
from urllib.parse import urlsplit

prog = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
state = Path(os.environ["SHIM_STATE"])
state.mkdir(parents=True, exist_ok=True)
with open(os.environ["SHIM_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"prog": prog, "argv": argv, "neon_key_in_env": bool(os.environ.get("NEON_API_KEY"))}) + "\n")
secret = os.environ.get("SHIM_SECRET", "")

def out(obj):
    print(json.dumps(obj))
    sys.exit(0)

def fail(msg, code=254):
    print(msg, file=sys.stderr)
    sys.exit(code)

def hosts():
    p = state / "hosts"
    return set(p.read_text().split()) if p.exists() else set()

def add_host(h):
    with open(state / "hosts", "a") as fh:
        fh.write(h + "\n")

if prog == "neonctl":
    if argv[:2] == ["projects", "list"]:
        p = state / "neon_project.json"
        out({"projects": [json.loads(p.read_text())] if p.exists() else []})
    if argv[:2] == ["projects", "create"]:
        name = argv[argv.index("--name") + 1]
        project = {"id": "proj-quiet-sky-1234", "name": name, "region_id": argv[argv.index("--region-id") + 1],
                   "pg_version": 17}
        (state / "neon_project.json").write_text(json.dumps(project))
        add_host(os.environ["SHIM_NEON_HOST"])
        out({"project": project, "connection_uris": [{"connection_uri":
             f"postgresql://neondb_owner:{secret}@{os.environ['SHIM_NEON_HOST']}-pooler/neondb?sslmode=require"}]})
    if argv[0] == "connection-string":
        print(f"postgresql://neondb_owner:{secret}@{os.environ['SHIM_NEON_HOST']}/neondb?sslmode=require")
        sys.exit(0)
    fail("unknown neonctl call", 1)

if prog == "aws":
    if argv[:2] == ["sts", "get-caller-identity"]:
        out({"Account": "123456789012", "UserId": "AIDFAKE", "Arn": "arn:aws:iam::123456789012:user/fixture"})
    if argv[:2] == ["rds", "describe-db-instances"]:
        p = state / "rds.json"
        if not p.exists():
            fail("An error occurred (DBInstanceNotFound) when calling the DescribeDBInstances operation: not found")
        out({"DBInstances": [json.loads(p.read_text())]})
    if argv[:2] == ["ec2", "describe-vpcs"]:
        out({"Vpcs": [{"VpcId": "vpc-0default", "IsDefault": True}]})
    if argv[:2] == ["ec2", "describe-security-groups"]:
        out({"SecurityGroups": [{"GroupId": "sg-0graph"}] if (state / "sg").exists() else []})
    if argv[:2] == ["ec2", "create-security-group"]:
        (state / "sg").write_text("sg-0graph")
        out({"GroupId": "sg-0graph"})
    if argv[:2] == ["ec2", "authorize-security-group-ingress"]:
        out({"Return": True})
    if argv[:2] == ["rds", "create-db-instance"]:
        inst = {"DBInstanceIdentifier": argv[argv.index("--db-instance-identifier") + 1], "DBInstanceStatus": "available",
                "Endpoint": {"Address": os.environ["SHIM_RDS_HOST"], "Port": 5432},
                "MasterUserSecret": {"SecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds-graph"}}
        (state / "rds.json").write_text(json.dumps(inst))
        add_host(os.environ["SHIM_RDS_HOST"])
        out({"DBInstance": inst})
    if argv[:3] == ["rds", "wait", "db-instance-available"]:
        sys.exit(0)
    if argv[:2] == ["secretsmanager", "get-secret-value"]:
        print(json.dumps({"username": "sdlc", "password": secret}))
        sys.exit(0)
    if argv[:2] == ["cloudformation", "describe-stacks"]:
        if not (state / "stack").exists():
            fail("An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id FactoryGraphDb does not exist")
        out({"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": [
            {"OutputKey": "ClusterEndpoint", "OutputValue": os.environ["SHIM_AURORA_HOST"]},
            {"OutputKey": "ClusterPort", "OutputValue": "5432"},
            {"OutputKey": "SecretArn", "OutputValue": "arn:aws:secretsmanager:us-east-1:123456789012:secret:aurora-graph"}]}]})
    fail("unknown aws call", 1)

if prog == "gcloud":
    if argv[:3] == ["spanner", "instances", "describe"]:
        if not (state / "instance").exists():
            fail("ERROR: (gcloud.spanner.instances.describe) NOT_FOUND", 1)
        out({"name": argv[3]})
    if argv[:3] == ["spanner", "instances", "create"]:
        (state / "instance").write_text(argv[3])
        out({"name": argv[3]})
    if argv[:3] == ["spanner", "databases", "describe"]:
        if not (state / "database").exists():
            fail("ERROR: (gcloud.spanner.databases.describe) NOT_FOUND", 1)
        out({"name": argv[3]})
    if argv[:3] == ["spanner", "databases", "create"]:
        ddl = argv[argv.index("--ddl-file") + 1]
        (state / "database").write_text(Path(ddl).read_text())
        out({"name": argv[3]})
    fail("unknown gcloud call", 1)

if prog == "uv":
    i = argv.index("sdlc-graph")
    args = argv[i + 1:]
    url = os.environ.get("DATABASE_URL", "")
    with open(state / "uv_urls", "a") as fh:  # which host the child saw, and whether the secret reached it by env
        fh.write(json.dumps({"host": urlsplit(url).hostname, "secret_in_env": bool(secret) and secret in url}) + "\n")
    host = urlsplit(url).hostname or ""
    reachable = host in hosts()
    inited = (state / f"inited_{host}").exists()
    if args[:1] == ["status"]:
        out({"configured": reachable and inited, "enabled": True, "provider": "fake", "reachable": reachable,
             "schema_version": "010" if inited else None, "snapshots": 0 if inited else None,
             "nodes": 1 if inited else None, "url": "postgresql://***@" + host, "hint": None,
             "error": None if reachable else "OperationalError: connection refused"})
    if args[:2] == ["db", "init"]:
        if not reachable:
            fail("database_unavailable", 5)
        (state / f"inited_{host}").write_text("1")
        out({"schema": "public", "applied": [f"{n:03d}_x.sql" for n in range(1, 11)], "tables": ["node"]})
    if args[:1] == ["provisioned"]:
        key = "|".join(args[args.index(k) + 1] for k in ("--provider", "--where", "--schema"))
        p = state / "evidence.json"
        rows = json.loads(p.read_text()) if p.exists() else []
        if key in rows:
            out({"status": "already_there", "slug": "repo-proj", "evidence_id": rows.index(key) + 1})
        rows.append(key)
        p.write_text(json.dumps(rows))
        out({"status": "recorded", "slug": "repo-proj", "evidence_id": len(rows)})
    if args[:2] == ["spanner", "ddl"]:
        Path(args[args.index("--out") + 1]).write_text("CREATE TABLE node (id STRING(36)) PRIMARY KEY (id);\n")
        out({"ok": True, "statements": 1})
    if args[:2] == ["spanner", "export"]:
        out({"ok": True, "rows_loaded": {"node": 1, "snapshot": 1}})
    fail("unknown sdlc-graph call", 2)

if prog == "docker":
    if os.environ.get("SHIM_DOCKER_SANDBOXED"):  # what docker prints as a child of a sandboxed command
        fail("Error loading config file: open ~/.docker/config.json: operation not permitted\n"
             "unknown shorthand flag: 'p' in -p", 125)
    fail("docker must not be called here", 1)
fail("unknown program", 1)
'''


class Fake:
    """A project with fake CLIs on PATH."""

    def __init__(self, repo, tmp_path: Path) -> None:
        self.repo = repo
        self.bin = tmp_path / "fakebin"
        self.bin.mkdir()
        self.state = tmp_path / "fakestate"
        self.log = tmp_path / "fakelog.jsonl"
        shim = f"#!{sys.executable}\n" + SHIM
        for prog in ("neonctl", "aws", "gcloud", "uv", "docker"):
            p = self.bin / prog
            p.write_text(shim)
            p.chmod(0o755)
        self.secret = "pw" + secrets.token_hex(12)
        self.neon_key = "napi" + secrets.token_hex(16)

    def env(self, **extra: str) -> dict[str, str]:
        e = self.repo.env(SHIM_STATE=str(self.state), SHIM_LOG=str(self.log), SHIM_SECRET=self.secret,
                          SHIM_NEON_HOST=NEON_HOST, SHIM_RDS_HOST=RDS_HOST, SHIM_AURORA_HOST=AURORA_HOST)
        e["PATH"] = str(self.bin) + os.pathsep + e.get("PATH", "")
        e.pop("NEON_API_KEY", None)
        e.update(extra)
        return e

    def run(self, *args: str, env: dict | None = None, stdin: str | None = None):
        p = subprocess.run([sys.executable, "-I", str(GRAPH_PY), *args], capture_output=True, text=True,
                           cwd=self.repo.path, env=env or self.env(), input=stdin, timeout=120)
        return p.returncode, p.stdout + p.stderr

    def calls(self, prog: str | None = None) -> list[list[str]]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [r["argv"] for r in rows if prog is None or r["prog"] == prog]

    def sdlc_graph_calls(self) -> list[list[str]]:
        return [a[a.index("sdlc-graph") + 1:] for a in self.calls("uv")]

    def reset_log(self) -> None:
        if self.log.exists():
            self.log.unlink()

    def assert_no_secret(self, text: str) -> None:
        for s in (self.secret, self.neon_key):
            assert s not in text, "a secret was printed"
        for argv in self.calls():
            assert not any(self.secret in a or self.neon_key in a for a in argv), argv
        sdlc_json = self.repo.read(".claude/sdlc.json")
        assert self.secret not in sdlc_json
        assert self.secret not in self.repo.git("log", "-p", "--all", check=False)

    def stored_url(self) -> str:
        return json.loads(self.repo.read(".claude/settings.local.json"))["env"]["DATABASE_URL"]

    def mark_ledger(self, host: str = LEDGER_HOST) -> None:
        """A reachable, initialised Postgres ledger at `host` (for spanner)."""
        self.state.mkdir(exist_ok=True)
        with open(self.state / "hosts", "a") as fh:
            fh.write(host + "\n")
        (self.state / f"inited_{host}").write_text("1")


@pytest.fixture
def fake(repo, tmp_path) -> Fake:
    return Fake(repo, tmp_path)


def _graphcfg():
    spec = importlib.util.spec_from_file_location("graphcfg_under_test", PACK / "sdlc_graph" / "graphcfg.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- the block


def test_graph_block_defaults_are_on_local_and_database_url():
    block, problems = cl.graph_block(None)
    assert problems == []
    assert block == {"enabled": True, "provider": "local", "url_env": "DATABASE_URL",
                     "spanner": {"project": "", "instance": "", "database": ""}}
    assert cl.DEFAULTS["graph"] == block


def test_graph_block_defaults_match_the_graph_package():
    gc = _graphcfg()
    assert gc.GRAPH_DEFAULTS == cl.GRAPH_DEFAULTS and gc.PROVIDERS == cl.GRAPH_PROVIDERS
    for bad in ({"enabled": "yes"}, {"provider": "sqlite"}, {"url_env": "lower-case"}, {"spanner": []}, []):
        assert gc.validate(bad) == cl.graph_block(bad)


@pytest.mark.parametrize("bad, problem", [
    ({"enabled": "yes"}, "graph.enabled must be true or false"),
    ({"provider": "sqlite"}, "graph.provider must be one of local, neon, aws, spanner"),
    ({"url_env": "database-url"}, "graph.url_env must be an environment variable name"),
    ({"spanner": {"project": 3}}, "graph.spanner must be an object of strings"),
    ("on", "graph must be an object"),
])
def test_graph_block_validation_falls_back_and_reports(bad, problem):
    block, problems = cl.graph_block(bad)
    assert any(p.startswith(problem) for p in problems), problems
    assert block["enabled"] is True and block["provider"] in cl.GRAPH_PROVIDERS


def test_graph_block_keeps_good_keys_next_to_a_bad_one():
    block, problems = cl.graph_block({"enabled": False, "provider": "neon", "url_env": "GRAPH_DB",
                                      "spanner": {"project": "p"}, "future": 1})
    assert problems == []
    assert block["enabled"] is False and block["provider"] == "neon" and block["url_env"] == "GRAPH_DB"
    assert block["spanner"] == {"project": "p", "instance": "", "database": ""} and block["future"] == 1


def test_a_bad_graph_block_is_a_config_error(repo):
    repo.set_config(graph={"enabled": "sometimes"})
    cfg = cl.load_config(str(repo.path))
    assert "graph.enabled must be true or false" in cfg["_config_error"]
    assert cfg["graph"]["enabled"] is True


def test_resolve_url_order_is_the_same_in_the_script_and_the_package(repo, monkeypatch):
    spec = importlib.util.spec_from_file_location("graph_script_under_test", GRAPH_PY)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    gc = _graphcfg()
    graph = cl.graph_block(None)[0]
    root = str(repo.path)
    cases = []
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cases.append(("default", None))
    monkeypatch.setenv("DATABASE_URL", gc.DEFAULT_DATABASE_URL)
    cases.append(("env:DATABASE_URL", None))  # the .mcp.json default with nothing stored
    repo.write(".env", "DATABASE_URL='postgresql://u:p@dotenv.example.test/db'\n")
    cases.append((".env", "postgresql://u:p@dotenv.example.test/db"))
    repo.write(".claude/settings.local.json", json.dumps({"env": {"DATABASE_URL": "postgresql://u:p@local.example.test/db"}}))
    cases.append((".claude/settings.local.json", "postgresql://u:p@local.example.test/db"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@shell.example.test/db")
    cases.append(("env:DATABASE_URL", "postgresql://u:p@shell.example.test/db"))
    # replay each state in order and compare both implementations at that state
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (repo.path / ".env").unlink()
    (repo.path / ".claude" / "settings.local.json").unlink()
    states = [
        lambda: monkeypatch.delenv("DATABASE_URL", raising=False),
        lambda: monkeypatch.setenv("DATABASE_URL", gc.DEFAULT_DATABASE_URL),
        lambda: repo.write(".env", "DATABASE_URL='postgresql://u:p@dotenv.example.test/db'\n"),
        lambda: repo.write(".claude/settings.local.json", json.dumps({"env": {"DATABASE_URL": "postgresql://u:p@local.example.test/db"}})),
        lambda: monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@shell.example.test/db"),
    ]
    for (source, url), apply in zip(cases, states):
        apply()
        got_pkg = gc.resolve_url(root, dict(os.environ))
        got_script = script.resolve_url(root, graph)
        assert got_pkg == got_script, (source, got_pkg, got_script)
        assert got_pkg[1] == source and (url is None or got_pkg[0] == url)


# --------------------------------------------------------------------------- init writes the block


def _init(root: Path, *args: str, env: dict | None = None):
    p = subprocess.run([sys.executable, "-I", str(INIT_PY), "--root", str(root), "--allow-partial-baseline", *args],
                       capture_output=True, text=True, env=env, timeout=300)
    return p.returncode, p.stdout + p.stderr


def test_init_dry_run_shows_the_block_and_the_defaults(uninit):
    uninit.write("pyproject.toml", "[project]\nname = 'x'\nversion = '0'\n")
    rc, out = _init(uninit.path, "--dry-run", env=uninit.env())
    assert rc == 0, out
    assert "graph: on, provider local, url_env DATABASE_URL  (defaults: on, local;" in out
    assert ('.claude/sdlc.json "graph": {"enabled": true, "provider": "local", "url_env": "DATABASE_URL", '
            '"spanner": {"project": "", "instance": "", "database": ""}}') in out
    assert not (uninit.path / ".claude" / "sdlc.json").exists()


def test_init_writes_the_answers_and_never_a_url(uninit):
    uninit.write("pyproject.toml", "[project]\nname = 'x'\nversion = '0'\n")
    env = uninit.env(DATABASE_URL="postgresql://u:" + secrets.token_hex(8) + "@db.example.test/x")
    rc, out = _init(uninit.path, "--graph", "off", "--graph-provider", "neon", env=env)
    assert "graph: off, provider neon" in out and "(as answered;" in out, out
    cfg = json.loads(uninit.read(".claude/sdlc.json"))
    assert cfg["graph"] == {"enabled": False, "provider": "neon", "url_env": "DATABASE_URL",
                            "spanner": {"project": "", "instance": "", "database": ""}}
    assert "postgresql://" not in uninit.read(".claude/sdlc.json")


def test_init_refuses_a_bad_answer(uninit):
    uninit.write("pyproject.toml", "[project]\nname = 'x'\n")
    rc, out = _init(uninit.path, "--dry-run", "--graph-provider", "mysql", env=uninit.env())
    assert rc == 2 and "--graph-provider takes one of local, neon, aws, spanner" in out


def test_init_upgrade_adds_the_block_to_an_older_config(repo):
    cfg = json.loads(repo.read(".claude/sdlc.json"))
    cfg.pop("graph", None)
    repo.write(".claude/sdlc.json", json.dumps(cfg))
    rc, out = _init(repo.path, "--upgrade", "--stack", "python", env=repo.env())
    assert "+keys:" in out and "graph" in out, out
    assert json.loads(repo.read(".claude/sdlc.json"))["graph"]["enabled"] is True


# --------------------------------------------------------------------------- on, off, status, url


def test_off_then_status_says_graph_off_without_touching_the_database(fake):
    rc, out = fake.run("off")
    assert rc == 0 and out.startswith("graph off: every graph step is skipped and /factory:orchestrate refuses"), out
    assert json.loads(fake.repo.read(".claude/sdlc.json"))["graph"]["enabled"] is False
    rc, out = fake.run("status")
    assert rc == 0 and out.strip() == "graph off: every graph step is skipped (/factory:graph on turns it back on)"
    assert fake.calls() == []  # nothing ran: no uv, no database
    rc, out = fake.run("off")
    assert "graph already off" in out
    rc, out = fake.run("on")
    assert rc == 0 and out.startswith("graph on (provider local)")
    assert json.loads(fake.repo.read(".claude/sdlc.json"))["graph"]["enabled"] is True


def test_on_off_keep_every_other_key(fake):
    before = json.loads(fake.repo.read(".claude/sdlc.json"))
    fake.run("off")
    after = json.loads(fake.repo.read(".claude/sdlc.json"))
    after_graph = after.pop("graph")
    before.pop("graph", None)
    assert after == before and after_graph["enabled"] is False and after_graph["url_env"] == "DATABASE_URL"


def test_status_unreachable_names_provision_and_the_variable(fake):
    rc, out = fake.run("status")
    assert rc == 1, out
    assert "graph on but not reachable: run /factory:graph provision local or set DATABASE_URL" in out
    assert fake.sdlc_graph_calls() == [["status", "--json"]]


def test_provision_is_refused_while_the_graph_is_off(fake):
    fake.run("off")
    rc, out = fake.run("provision", "neon", "--region-id", "aws-us-east-2")
    assert rc == 2 and "refused: graph off" in out and "/factory:graph on" in out
    assert fake.calls() == []


def test_url_from_file_stores_it_deletes_the_file_and_prints_nothing_secret(fake):
    url = f"postgresql://neondb_owner:{fake.secret}@{NEON_HOST}/neondb?sslmode=require"
    fake.repo.write(".sdlc-stage/graph-url", url + "\n")
    rc, out = fake.run("url", "--from-file", ".sdlc-stage/graph-url")
    assert rc == 0, out
    assert "DATABASE_URL written in .claude/settings.local.json (value not shown)" in out
    assert not (fake.repo.path / ".sdlc-stage" / "graph-url").exists()
    assert fake.stored_url() == url
    fake.assert_no_secret(out)
    assert fake.repo.git("check-ignore", ".claude/settings.local.json").strip() == ".claude/settings.local.json"


def test_url_into_dotenv_adds_the_gitignore_line(fake):
    url = f"postgresql://u:{fake.secret}@{NEON_HOST}/neondb"
    rc, out = fake.run("url", "--env-file", ".env", stdin=url + "\n")
    assert rc == 0 and "added .env to .gitignore" in out, out
    assert f"DATABASE_URL={url}" in fake.repo.read(".env")
    assert (os.stat(fake.repo.path / ".env").st_mode & 0o777) == 0o600
    fake.assert_no_secret(out)
    rc, out = fake.run("url", "--env-file", ".env", stdin=url + "\n")
    assert "DATABASE_URL unchanged in .env" in out


def test_url_refuses_what_is_not_a_postgres_url(fake):
    rc, out = fake.run("url", stdin="mysql://u:p@h/db\n")
    assert rc == 2 and "not a PostgreSQL connection string" in out


# --------------------------------------------------------------------------- neon


def test_provision_neon_with_neonctl(fake):
    env = fake.env(NEON_API_KEY=fake.neon_key)
    rc, out = fake.run("provision", "neon", "--region-id", "aws-us-east-2", env=env)
    assert rc == 0, out
    assert fake.calls("neonctl") == [
        ["projects", "list", "--output", "json"],
        ["projects", "create", "--name", "proj-graph", "--region-id", "aws-us-east-2", "--output", "json"],
        ["connection-string", "--project-id", "proj-quiet-sky-1234", "--database-name", "neondb"],
    ]
    assert fake.sdlc_graph_calls() == [
        ["status", "--json"], ["db", "init", "--json"], ["status", "--json"],
        ["provisioned", "--provider", "neon", "--where", f"neon {NEON_HOST}:5432/neondb", "--schema", "010",
         "--repo-dir", str(fake.repo.path), "--json", "--extra",
         json.dumps({"project_id": "proj-quiet-sky-1234", "region": "aws-us-east-2"})],
    ]
    seen = [json.loads(line) for line in (fake.state / "uv_urls").read_text().splitlines()]
    assert all(s["host"] == NEON_HOST and s["secret_in_env"] for s in seen)  # the URL reached sdlc-graph by env only
    assert fake.stored_url() == f"postgresql://neondb_owner:{fake.secret}@{NEON_HOST}/neondb?sslmode=require"
    assert json.loads(fake.repo.read(".claude/sdlc.json"))["graph"]["provider"] == "neon"
    assert "evidence: graph_provisioned recorded on repo-proj" in out and f"provisioned: neon {NEON_HOST}" in out
    fake.assert_no_secret(out)

    fake.reset_log()
    rc, out = fake.run("provision", "neon", "--region-id", "aws-us-east-2", env=env)
    assert rc == 0, out
    assert fake.calls("neonctl") == []  # the stored URL answers: nothing is created
    assert "a stored URL answers (from .claude/settings.local.json): no Neon project is created" in out
    assert "evidence: graph_provisioned already there" in out and f"already there: neon {NEON_HOST}" in out
    fake.assert_no_secret(out)


def test_provision_neon_reuses_a_project_found_by_name(fake):
    fake.state.mkdir()
    (fake.state / "neon_project.json").write_text(json.dumps({"id": "proj-quiet-sky-1234", "name": "proj-graph",
                                                              "region_id": "aws-us-east-2"}))
    (fake.state / "hosts").write_text(NEON_HOST + "\n")
    rc, out = fake.run("provision", "neon", env=fake.env(NEON_API_KEY=fake.neon_key))
    assert rc == 0, out
    assert [c[:2] for c in fake.calls("neonctl")] == [["projects", "list"], ["connection-string", "--project-id"]]
    assert "neon project proj-graph already there" in out


def test_provision_neon_needs_the_key_in_the_shell_and_the_region(fake):
    rc, out = fake.run("provision", "neon", "--region-id", "aws-us-east-2")
    assert rc == 2 and "NEON_API_KEY is not set: export it in your own shell" in out and "never paste it in chat" in out
    rc, out = fake.run("provision", "neon", env=fake.env(NEON_API_KEY=fake.neon_key))
    assert rc == 2 and "the Neon region is a decision: pass --region-id" in out
    assert [c[:2] for c in fake.calls("neonctl")] == [["projects", "list"]]
    assert not any(r["neon_key_in_env"] is False for r in map(json.loads, fake.log.read_text().splitlines())
                   if r["prog"] == "neonctl")
    fake.assert_no_secret(out)


# --------------------------------------------------------------------------- aws


def test_provision_aws_demo(fake):
    args = ("provision", "aws", "--shape", "aws-demo", "--region", "us-east-1", "--account", "123456789012",
            "--payer", "platform team", "--operator-cidr", "203.0.113.7/32")
    rc, out = fake.run(*args)
    assert rc == 0, out
    assert fake.calls("aws") == [
        ["sts", "get-caller-identity", "--output", "json"],
        ["rds", "describe-db-instances", "--db-instance-identifier", "factory-graph-proj", "--region", "us-east-1", "--output", "json"],
        ["ec2", "describe-vpcs", "--filters", "Name=is-default,Values=true", "--region", "us-east-1", "--output", "json"],
        ["ec2", "describe-security-groups", "--filters", "Name=group-name,Values=factory-graph-proj-sg",
         "Name=vpc-id,Values=vpc-0default", "--region", "us-east-1", "--output", "json"],
        ["ec2", "create-security-group", "--group-name", "factory-graph-proj-sg", "--description",
         "factory graph db: 5432 from the operator only", "--vpc-id", "vpc-0default", "--region", "us-east-1", "--output", "json"],
        ["ec2", "authorize-security-group-ingress", "--group-id", "sg-0graph", "--protocol", "tcp", "--port", "5432",
         "--cidr", "203.0.113.7/32", "--region", "us-east-1", "--output", "json"],
        ["rds", "create-db-instance", "--db-instance-identifier", "factory-graph-proj", "--engine", "postgres",
         "--engine-version", "16", "--db-instance-class", "db.t4g.micro", "--allocated-storage", "20", "--db-name", "sdlc",
         "--master-username", "sdlc", "--manage-master-user-password", "--publicly-accessible", "--vpc-security-group-ids",
         "sg-0graph", "--storage-encrypted", "--backup-retention-period", "1", "--tags", "Key=created-by,Value=factory-graph",
         "Key=payer,Value=platform-team", "--region", "us-east-1", "--output", "json"],
        ["rds", "wait", "db-instance-available", "--db-instance-identifier", "factory-graph-proj", "--region", "us-east-1"],
        ["rds", "describe-db-instances", "--db-instance-identifier", "factory-graph-proj", "--region", "us-east-1", "--output", "json"],
        ["secretsmanager", "get-secret-value", "--secret-id", "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds-graph",
         "--region", "us-east-1", "--query", "SecretString", "--output", "text"],
    ]
    assert fake.stored_url() == f"postgresql://sdlc:{fake.secret}@{RDS_HOST}:5432/sdlc?sslmode=require"
    assert [c[0] for c in fake.sdlc_graph_calls()] == ["status", "db", "status", "provisioned"]
    assert f"provisioned: aws {RDS_HOST}:5432/sdlc" in out
    fake.assert_no_secret(out)

    fake.reset_log()
    rc, out = fake.run(*args)
    assert rc == 0 and fake.calls("aws") == [] and f"already there: aws {RDS_HOST}:5432/sdlc" in out, out
    fake.assert_no_secret(out)


def test_provision_aws_demo_resumes_an_instance_that_exists(fake):
    fake.run("provision", "aws", "--shape", "aws-demo", "--region", "us-east-1", "--account", "123456789012",
             "--operator-cidr", "203.0.113.7/32")
    data = json.loads(fake.repo.read(".claude/settings.local.json"))
    data["env"].pop("DATABASE_URL")
    fake.repo.write(".claude/settings.local.json", json.dumps(data))  # the URL was lost, the instance is there
    fake.reset_log()
    rc, out = fake.run("provision", "aws", "--shape", "aws-demo", "--region", "us-east-1", "--account", "123456789012",
                       "--operator-cidr", "203.0.113.7/32")
    assert rc == 0, out
    assert not any(c[:2] in (["rds", "create-db-instance"], ["ec2", "create-security-group"]) for c in fake.calls("aws"))
    assert "rds instance factory-graph-proj already there" in out and "evidence: graph_provisioned already there" in out
    assert fake.stored_url().endswith(f"@{RDS_HOST}:5432/sdlc?sslmode=require")


def test_provision_aws_refuses_the_wrong_account_and_missing_decisions(fake):
    rc, out = fake.run("provision", "aws", "--shape", "aws-demo", "--region", "us-east-1", "--account", "999999999999",
                       "--operator-cidr", "203.0.113.7/32")
    assert rc == 2 and "the AWS credentials are for account 123456789012, not 999999999999" in out
    assert fake.calls("aws") == [["sts", "get-caller-identity", "--output", "json"]]
    rc, out = fake.run("provision", "aws", "--shape", "aws-demo", "--account", "123456789012")
    assert rc == 2 and "--region is a decision for aws" in out
    rc, out = fake.run("provision", "aws", "--region", "us-east-1", "--account", "123456789012")
    assert rc == 2 and "the shape is a decision: --shape aws-demo" in out
    rc, out = fake.run("provision", "aws", "--shape", "aws-demo", "--region", "us-east-1", "--account", "123456789012")
    assert rc == 2 and "pass --operator-cidr <your public IP>/32" in out


def test_provision_aws_private_writes_the_stack_then_connects_once_deployed(fake):
    args = ("provision", "aws", "--shape", "aws-private", "--region", "us-east-1", "--account", "123456789012",
            "--payer", "data platform", "--vpc-id", "vpc-0private")
    rc, out = fake.run(*args)
    assert rc == 0, out
    assert fake.calls("aws") == [
        ["sts", "get-caller-identity", "--output", "json"],
        ["cloudformation", "describe-stacks", "--stack-name", "FactoryGraphDb", "--region", "us-east-1", "--output", "json"],
    ]
    for name in ("app.py", "cdk.json", "requirements.txt", "README.md"):
        assert f"+ infra/graph-db/{name}" in out
    app = fake.repo.read("infra/graph-db/app.py")
    compile(app, "app.py", "exec")
    assert "serverless_v2" in app and "PRIVATE_WITH_EGRESS" in app and "from_generated_secret" in app
    assert json.loads(fake.repo.read("infra/graph-db/cdk.json"))["context"] == {
        "account": "123456789012", "region": "us-east-1", "vpc_id": "vpc-0private", "payer": "data platform"}
    assert "pending: deploy infra/graph-db through your pipeline (stack FactoryGraphDb)" in out
    assert fake.sdlc_graph_calls() == [["status", "--json"]] or fake.sdlc_graph_calls() == []

    rc, out = fake.run(*args)  # not deployed yet: the files are there, nothing changes
    assert "= infra/graph-db/app.py" in out and "pending:" in out

    (fake.state / "stack").write_text("1")  # the team's pipeline deployed it
    with open(fake.state / "hosts", "a") as fh:
        fh.write("localhost\n")
    fake.reset_log()
    rc, out = fake.run(*args, "--via-host", "localhost", "--via-port", "15432")
    assert rc == 0, out
    assert fake.calls("aws")[1:] == [
        ["cloudformation", "describe-stacks", "--stack-name", "FactoryGraphDb", "--region", "us-east-1", "--output", "json"],
        ["secretsmanager", "get-secret-value", "--secret-id", "arn:aws:secretsmanager:us-east-1:123456789012:secret:aurora-graph",
         "--region", "us-east-1", "--query", "SecretString", "--output", "text"],
    ]
    assert fake.stored_url() == f"postgresql://sdlc:{fake.secret}@localhost:15432/sdlc?sslmode=require"
    assert f"provisioned: aws {AURORA_HOST}:5432/sdlc" in out
    fake.assert_no_secret(out)


# --------------------------------------------------------------------------- spanner


SPANNER = ("provision", "spanner", "--project", "acme-graph", "--instance", "factory-graph", "--database", "sdlc",
           "--config", "regional-us-central1", "--processing-units", "100")


def test_provision_spanner_needs_the_postgres_ledger_first(fake):
    rc, out = fake.run(*SPANNER)
    assert rc == 2 and "the ledger runs on PostgreSQL with pgvector and Spanner only mirrors the graph" in out
    assert fake.calls("gcloud") == []


def test_provision_spanner(fake):
    ledger = f"postgresql://sdlc:{fake.secret}@{LEDGER_HOST}:5432/sdlc"
    fake.repo.write(".claude/settings.local.json", json.dumps({"env": {"DATABASE_URL": ledger}}))
    fake.mark_ledger()
    rc, out = fake.run(*SPANNER)
    assert rc == 0, out
    gcloud = fake.calls("gcloud")
    assert gcloud[:3] == [
        ["spanner", "instances", "describe", "factory-graph", "--project", "acme-graph", "--format", "json"],
        ["spanner", "instances", "create", "factory-graph", "--project", "acme-graph", "--config", "regional-us-central1",
         "--processing-units", "100", "--edition", "ENTERPRISE", "--description", "factory graph mirror", "--format", "json"],
        ["spanner", "databases", "describe", "sdlc", "--instance", "factory-graph", "--project", "acme-graph", "--format", "json"],
    ]
    create = gcloud[3]
    assert create[:7] == ["spanner", "databases", "create", "sdlc", "--instance", "factory-graph", "--project"]
    assert create[8] == "--ddl-file" and create[-2:] == ["--format", "json"] and len(gcloud) == 4
    assert (fake.state / "database").read_text().startswith("CREATE TABLE node")
    sg = fake.sdlc_graph_calls()
    assert sg[0] == ["status", "--json"] and sg[1][:3] == ["spanner", "ddl", "--out"]
    assert sg[2] == ["spanner", "export", "--project", "acme-graph", "--instance", "factory-graph", "--database", "sdlc", "--json"]
    assert sg[3][:6] == ["provisioned", "--provider", "spanner", "--where",
                         "spanner projects/acme-graph/instances/factory-graph/databases/sdlc", "--schema"]
    graph = json.loads(fake.repo.read(".claude/sdlc.json"))["graph"]
    assert graph["provider"] == "spanner"
    assert graph["spanner"] == {"project": "acme-graph", "instance": "factory-graph", "database": "sdlc"}
    assert "the ledger stays on PostgreSQL" in out and "provisioned: spanner projects/acme-graph" in out
    fake.assert_no_secret(out)

    fake.reset_log()
    rc, out = fake.run("provision", "spanner", "--config", "regional-us-central1", "--processing-units", "100")
    assert rc == 0, out  # project, instance and database now come from graph.spanner
    assert [c[:3] for c in fake.calls("gcloud")] == [["spanner", "instances", "describe"], ["spanner", "databases", "describe"]]
    assert "already there: spanner projects/acme-graph/instances/factory-graph/databases/sdlc (the mirror was refreshed)" in out
    assert "evidence: graph_provisioned already there" in out


def test_provision_spanner_asks_for_its_decisions(fake):
    rc, out = fake.run("provision", "spanner", "--project", "p", "--instance", "i", "--database", "d")
    assert rc == 2 and "--config is a decision for spanner" in out


# --------------------------------------------------------------------------- the skill


def test_the_skill_states_the_spanner_limit_and_every_procedure():
    text = " ".join((PACK / "skills" / "graph" / "SKILL.md").read_text().split())
    assert "Spanner is the graph mirror" in text and "not the ledger's store" in text
    assert "A native Spanner ledger is a later adapter" in text
    for word in ("### local", "### neon", "### aws", "### spanner", "graph_provisioned {provider, where, schema}",
                 "`console.neon.tech`, `*.neon.tech`", "`*.amazonaws.com`", "`*.googleapis.com`", "already there"):
        assert word in text, word
    raw = (PACK / "docs" / "graph-providers.md").read_text()
    docs = raw
    assert "A native Spanner ledger is a later adapter" in " ".join(raw.split())
    for heading in ("## local", "## neon", "## aws", "## spanner", "## The Spanner limit", "## Costs"):
        assert heading in docs, heading


def test_provision_local_in_the_sandbox_hands_docker_to_a_top_level_command(fake):
    # Review of a headless run: docker as a child of graph.py is sandboxed (~/.docker unreadable) and exits 125, while
    # the sandbox excludes only a top-level `docker compose *`. The script says so and prints the two commands.
    env = fake.env(SHIM_DOCKER_SANDBOXED="1")
    rc, out = fake.run("provision", "local", "--compose-project", "scratchg", env=env)
    assert rc == 1, out
    assert "docker compose up -d db failed (exit 125)" in out
    compose_file = PACK / "docker-compose.yml"
    assert f'next: docker compose --env-file .sdlc-stage/graph-compose.vars -f "{compose_file}" up -d db' in out
    assert "next: graph.py provision local --no-docker --compose-project scratchg" in out
    assert fake.repo.read(".sdlc-stage/graph-compose.vars").splitlines() == [
        "SDLC_PG_PORT=5435", "SDLC_PG_CONTAINER=scratchg-db", "COMPOSE_PROJECT_NAME=scratchg"]
    assert len(fake.calls("docker")) == 1
    assert fake.repo.git("check-ignore", ".sdlc-stage/graph-compose.vars").strip() == ".sdlc-stage/graph-compose.vars"

    fake.state.mkdir(exist_ok=True)
    with open(fake.state / "hosts", "a") as fh:  # the top-level docker compose started the database
        fh.write("localhost\n")
    fake.reset_log()
    rc, out = fake.run("provision", "local", "--no-docker", "--compose-project", "scratchg", env=env)
    assert rc == 0, out
    assert fake.calls("docker") == []  # --no-docker never calls Docker
    assert "db init: 10 DDL file(s) applied" in out and "evidence: graph_provisioned recorded" in out
    assert not (fake.repo.path / ".sdlc-stage" / "graph-compose.vars").exists()
    rc, out = fake.run("provision", "local", "--no-docker", "--compose-project", "scratchg", env=env)
    assert rc == 0 and "evidence: graph_provisioned already there" in out, out


def _settings_local_read_only(fake) -> None:
    """What the pack's sandbox does to .claude/settings.local.json: reads work, a write raises PermissionError."""
    fake.repo.write(".claude/settings.local.json", "{}\n")
    os.chmod(fake.repo.path / ".claude" / "settings.local.json", 0o444)


def test_provision_local_finishes_when_the_sandbox_denies_the_env_file(fake):
    # Review of a headless run: the sandbox denies writes to .claude/settings.local.json, and the script crashed with
    # a PermissionError after db init, before status and evidence. The shell's URL already leads to the database.
    _settings_local_read_only(fake)
    fake.state.mkdir(exist_ok=True)
    (fake.state / "hosts").write_text("localhost\n")
    url = f"postgresql://sdlc:{fake.secret}@localhost:5471/sdlc"
    rc, out = fake.run("provision", "local", env=fake.env(DATABASE_URL=url))
    assert rc == 0, out
    assert "not stored: .claude/settings.local.json cannot be written here" in out
    assert "This session reaches the database through env:DATABASE_URL" in out
    assert "evidence: graph_provisioned recorded" in out and "Traceback" not in out
    assert json.loads(fake.repo.read(".claude/settings.local.json")) == {}
    fake.assert_no_secret(out)


def test_a_url_with_no_other_source_fails_plainly_when_it_cannot_be_stored(fake):
    _settings_local_read_only(fake)
    url = f"postgresql://neondb_owner:{fake.secret}@{NEON_HOST}/neondb?sslmode=require"
    fake.repo.write(".sdlc-stage/graph-url", url + "\n")
    rc, out = fake.run("url", "--from-file", ".sdlc-stage/graph-url")
    assert rc == 1, out
    assert "nothing was stored; a human runs graph.py url in their own terminal" in out and "Traceback" not in out
    assert not (fake.repo.path / ".sdlc-stage" / "graph-url").exists()  # the staged secret is still deleted
    fake.assert_no_secret(out)
    rc, out = fake.run("provision", "neon", "--region-id", "aws-us-east-2", env=fake.env(NEON_API_KEY=fake.neon_key))
    assert rc == 1, out
    assert "the database is built but its URL is not stored; a human reruns this same provision command" in out
    fake.assert_no_secret(out)


def test_provision_local_no_docker_fails_plainly_when_nothing_answers(fake):
    rc, out = fake.run("provision", "local", "--no-docker", "--headless", "--timeout", "2")  # --headless: a no-op
    assert rc == 1, out
    assert "did not answer in 2s" in out and "docker compose --env-file .sdlc-stage/graph-compose.vars" in out
    assert fake.calls("docker") == []


def test_check_skill_permissions_covers_the_graph_skill():
    p = subprocess.run([sys.executable, "-I", str(PACK / "scripts" / "check_skill_permissions.py"), "--root", str(PACK)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and " 0 uncovered" in p.stdout, p.stdout + p.stderr
    p = subprocess.run([sys.executable, "-I", str(PACK / "scripts" / "check_skill_permissions.py"), "--root", str(PACK),
                        "--skills-dir", str(PACK / "skills" / "graph"), "--own-only", "--verbose"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout
    for cmd in ("graph.py\" provision local", "graph.py\" provision local --no-docker",
                "docker compose --env-file .sdlc-stage/graph-compose.vars -f", "graph.py\" provision neon --region-id", "graph.py\" provision aws --shape aws-demo",
                "graph.py\" provision aws --shape aws-private", "graph.py\" provision spanner --project",
                "graph.py\" url --from-file .sdlc-stage/graph-url", "curl -s https://checkip.amazonaws.com"):
        assert cmd in p.stdout, cmd
