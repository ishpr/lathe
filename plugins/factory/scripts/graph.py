#!/usr/bin/env python3
"""/factory:graph: turn the code graph and ledger on or off, store its connection string, and build its database.

Commands (one per run; the skill runs each on its own):

  status                   graph on or off, provider, reachable, schema version, snapshot and node counts
  migrate                  apply the migrations the database is behind by (sdlc-graph db init; idempotent); status
                           says "behind by N migrations" when there are some
  on | off               flip graph.enabled in .claude/sdlc.json (the only key this script writes there besides
                           graph.provider and graph.spanner, after a provision)
  url                      store a connection string in the local env file without printing it. Sources:
                           --from-file PATH (read, then deleted), --from-aws-secret ARN --region R,
                           --from-gcp-secret NAME --project P, or stdin (a hidden prompt on a terminal; a human runs
                           that form in their own terminal, never the agent)
  provision local          docker compose -f <plugin>/docker-compose.yml up -d db, wait for ready, sdlc-graph db init,
                           write the URL to the env file, status, evidence. Unattended. A reachable database at the
                           URL (CI's service container) skips Docker: db init only. When docker compose fails (in the
                           pack's sandbox docker runs only as a top-level command, which the sandbox excludes; as a
                           child of this script it cannot read ~/.docker), the script writes the compose variables to
                           .sdlc-stage/graph-compose.vars and prints the two `next:` commands: the top-level docker
                           compose line, then `provision local --no-docker`, which waits for the database without
                           calling Docker and goes on with db init.
  provision neon           neonctl with NEON_API_KEY from the shell: projects list (idempotence), projects create
                           --name <repo> --region-id <R>, connection-string (direct endpoint, not the pooler),
                           db init (it creates the vector extension), env file, status, evidence. With the Neon
                           MCP the skill creates the project itself and stores the URL with url --from-file.
  provision aws            --shape aws-demo: RDS for PostgreSQL 16 db.t4g.micro in the default VPC, port 5432 open to
                           --operator-cidr only, password managed by RDS in Secrets Manager. --shape aws-private: a
                           CDK stack (Aurora PostgreSQL Serverless v2, private subnets) written under infra/graph-db/
                           for the team's pipeline; once deployed, the next run connects from inside the VPC (or
                           through an SSM port forward, --via-host/--via-port). Both: the URL is assembled from the
                           secret and never printed, then db init (CREATE EXTENSION vector), env file, status,
                           evidence. --region and --account are required and checked against the credentials.
  provision spanner        the ledger stays on PostgreSQL (it must be reachable first); gcloud spanner instances
                           create (--edition ENTERPRISE: Spanner Graph needs it) and databases create --ddl-file with
                           the DDL of `sdlc-graph spanner ddl`, then `sdlc-graph spanner export` mirrors the graph.

Every provision run is idempotent: what exists is found and reused, and the evidence row graph_provisioned
{provider, where, schema} on the repo node is written once per database (a repeat says "already there").

The connection string never reaches sdlc.json, git, argv or this script's output: it is passed to child processes
in their environment only, and every line printed is redacted. The env file is .claude/settings.local.json
(env.<url_env>, the default) or .env (--env-file .env); either is gitignored, and the script adds the gitignore line
when it is missing.

Usage: graph.py [--root DIR] status [--json]
       graph.py [--root DIR] migrate
       graph.py [--root DIR] on|off
       graph.py [--root DIR] url [--from-file PATH | --from-aws-secret ARN --region R | --from-gcp-secret NAME --project P]
                                 [--env-file PATH]
       graph.py [--root DIR] provision local [--compose-project NAME] [--no-docker] [--env-file PATH] [--timeout S]
       graph.py [--root DIR] provision neon [--region-id R] [--name N] [--env-file PATH]
       graph.py [--root DIR] provision aws --shape aws-demo|aws-private --region R --account ID [--payer WHO]
                                 [--operator-cidr CIDR] [--vpc-id VPC] [--name ID] [--via-host H --via-port P]
                                 [--env-file PATH]
       graph.py [--root DIR] provision spanner --project P --instance I --database D --config C
                                 --processing-units N
--headless is accepted everywhere and changes nothing (the script never asks; the skill decides what is asked).
Exit: 0 ok (status: on and configured, or off), 1 a step failed or the graph is on but not usable, 2 refusal or
misuse (graph off for provision, a decision missing, wrong account).
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib")
)
import sdlc as cl

DEFAULT_URL = "postgresql://sdlc:sdlc@localhost:5435/sdlc"
DEFAULT_ENV_FILE = os.path.join(".claude", "settings.local.json")
OFF_LINE = "graph off: every graph step is skipped (/factory:graph on turns it back on)"
STACK_NAME = "FactoryGraphDb"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
SECRETS: set[str] = set()
_URI_USERINFO = re.compile(r"""([A-Za-z][A-Za-z0-9+.-]*://)[^\s/"']*@""")
_KV_PASSWORD = re.compile(r"(?i)(\bpassword\s*=\s*)('[^']*'|\S+)")
_JSON_PASSWORD = re.compile(r'(?i)("password"\s*:\s*)"[^"]*"')


class Refused(Exception):
    """A refusal or a missing decision: exit 2."""


class Failed(Exception):
    """A step failed: exit 1."""


class NotWritable(Failed):
    """The env file cannot be written here (the pack's sandbox denies writes to .claude/settings*.json)."""


# ---------- output and secrets ----------


def redact(text: object) -> str:
    out = str(text)
    for secret in sorted(SECRETS, key=len, reverse=True):
        if secret:
            out = out.replace(secret, "***")
    out = _URI_USERINFO.sub(r"\1***@", out)
    out = _KV_PASSWORD.sub(r"\1***", out)
    return _JSON_PASSWORD.sub(r'\1"***"', out)


def say(text: str = "") -> None:
    print(redact(text))


def keep_secret(url: str) -> str:
    """Remember the URL and its password so no printed line can carry them."""
    SECRETS.add(url)
    try:
        parts = urlsplit(url)
        # The bare password is redacted wherever it appears, unless it is too short to be told apart from words
        # (the local default's "sdlc"): the userinfo pattern still hides it inside any URL.
        if parts.password and len(parts.password) >= 8 and url != DEFAULT_URL:
            SECRETS.add(parts.password)
            SECRETS.add(quote(parts.password, safe=""))
    except ValueError:
        pass
    return url


def shown(url: str) -> str:
    """The URL with its credentials hidden (host, port and database stay readable)."""
    return _KV_PASSWORD.sub(r"\1***", _URI_USERINFO.sub(r"\1***@", url))


# ---------- the project ----------


def config_path(root: str) -> str:
    return os.path.join(root, cl.CONFIG_REL)


def read_config(root: str) -> dict:
    path = config_path(root)
    if not os.path.exists(path):
        raise Refused(
            f"no {cl.CONFIG_REL.replace(os.sep, '/')} here: run /factory:init first"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refused(
            f"{cl.CONFIG_REL.replace(os.sep, '/')} cannot be read as JSON ({exc}); repair it first"
        ) from exc
    if not isinstance(data, dict):
        raise Refused(
            f"{cl.CONFIG_REL.replace(os.sep, '/')}: the top level must be a JSON object"
        )
    return data


def graph_of(data: dict) -> tuple[dict, list[str]]:
    return cl.graph_block(data.get("graph"))


def write_graph(root: str, **changes: object) -> list[str]:
    """Set keys of the graph block (only those) and rewrite sdlc.json; returns the keys that changed."""
    data = read_config(root)
    block = data.get("graph") if isinstance(data.get("graph"), dict) else {}
    full, _ = cl.graph_block(block)
    changed = [k for k, v in changes.items() if full.get(k) != v]
    if not changed:
        return []
    block = dict(block)
    block.update(changes)
    for key, value in full.items():
        block.setdefault(key, value)
    data["graph"] = block
    with open(config_path(root), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    return changed


def repo_name(root: str) -> str:
    proc = subprocess.run(
        ["git", "-C", root, "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
    )
    url = proc.stdout.strip() if proc.returncode == 0 else ""
    m = re.search(r"([^/:]+?)(?:\.git)?/?$", url) if url else None
    return (m.group(1) if m else os.path.basename(os.path.realpath(root))) or "repo"


def slug(text: str, limit: int = 40) -> str:
    out = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")
    return out[:limit].strip("-") or "repo"


# ---------- the URL ----------


def from_settings_local(root: str, name: str) -> str | None:
    try:
        with open(
            os.path.join(root, ".claude", "settings.local.json"), encoding="utf-8"
        ) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    env = data.get("env") if isinstance(data, dict) else None
    value = env.get(name) if isinstance(env, dict) else None
    return value if isinstance(value, str) and value else None


def from_dotenv(root: str, name: str, path: str = ".env") -> str | None:
    try:
        with open(os.path.join(root, path), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for line in lines:
        m = re.match(
            r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line
        )
        if m and m.group(1) == name:
            value = m.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value or None
    return None


def resolve_url(root: str, graph: dict) -> tuple[str, str]:
    """Same order as sdlc_graph/graphcfg.resolve_url: the variable (unless it holds the local default), then
    .claude/settings.local.json env, then .env, then the default."""
    name = graph["url_env"]
    value = os.environ.get(name) or ""
    if value and value != DEFAULT_URL:
        return keep_secret(value), f"env:{name}"
    for source, stored in (
        (".claude/settings.local.json", from_settings_local(root, name)),
        (".env", from_dotenv(root, name)),
    ):
        if stored:
            return keep_secret(stored), source
    if value:
        return value, f"env:{name}"
    if name != "DATABASE_URL" and os.environ.get("DATABASE_URL"):
        return keep_secret(os.environ["DATABASE_URL"]), "env:DATABASE_URL"
    return DEFAULT_URL, "default"


def check_url(url: str) -> str:
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise Refused("not a PostgreSQL connection string") from exc
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise Refused(
            "not a PostgreSQL connection string (postgresql://user:password@host:port/database)"
        )
    return keep_secret(url)


def url_where(provider: str, url: str) -> str:
    p = urlsplit(url)
    return f"{provider} {p.hostname}:{p.port or 5432}/{(p.path or '/').lstrip('/') or 'postgres'}"


def gitignored(root: str, rel: str) -> str:
    """'' when git ignores rel (or root is not a checkout); else adds the line to .gitignore and says so."""
    proc = subprocess.run(
        ["git", "-C", root, "check-ignore", "-q", rel], capture_output=True, text=True
    )
    if proc.returncode != 1:
        return ""
    gi = os.path.join(root, ".gitignore")
    present = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    with open(gi, "a", encoding="utf-8") as fh:
        fh.write(
            ("" if not present or present.endswith("\n") else "\n")
            + rel.replace(os.sep, "/")
            + "\n"
        )
    return f"added {rel.replace(os.sep, '/')} to .gitignore"


def store_url(root: str, name: str, url: str, env_file: str | None) -> str:
    """Write name=url into the env file (JSON settings env, or a dotenv file). Returns one redacted line."""
    rel = os.path.normpath(env_file or DEFAULT_ENV_FILE)
    if os.path.isabs(rel) or rel.startswith(".."):
        raise Refused(
            "--env-file must be a path inside the repository (.claude/settings.local.json or .env)"
        )
    path = os.path.join(root, rel)
    try:
        return _store_url(root, rel, path, name, url)
    except PermissionError as exc:
        raise NotWritable(
            f"{rel.replace(os.sep, '/')} cannot be written here ({exc.strerror}; the pack's sandbox "
            f"denies writes to Claude Code's settings files)"
        ) from None


def _store_url(root: str, rel: str, path: str, name: str, url: str) -> str:
    os.makedirs(os.path.dirname(path) or root, exist_ok=True)
    created = not os.path.exists(path)
    if rel.endswith(".json"):
        data: dict = {}
        if not created:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as exc:
                raise Failed(
                    f"{rel} is not valid JSON ({exc}); repair it, then rerun"
                ) from exc
            if not isinstance(data, dict):
                raise Failed(f"{rel}: the top level must be a JSON object")
        env = data.get("env") if isinstance(data.get("env"), dict) else {}
        if env.get(name) == url:
            state = "unchanged"
        else:
            env[name] = url
            data["env"] = env
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2) + "\n")
            state = "written"
    else:
        lines = open(path, encoding="utf-8").read().splitlines() if not created else []
        value = (
            url
            if re.fullmatch(r"[A-Za-z0-9_@%:/?=&.+-]+", url)
            else '"' + url.replace('"', '\\"') + '"'
        )
        line = f"{name}={value}"
        pat = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=")
        if line in lines:
            state = "unchanged"
        else:
            lines = [ln for ln in lines if not pat.match(ln)] + [line]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            state = "written"
    if created:
        os.chmod(path, 0o600)
    note = gitignored(root, rel)
    return f"{name} {state} in {rel.replace(os.sep, '/')} (value not shown)" + (
        f"; {note}" if note else ""
    )


# ---------- child processes ----------


def need(program: str, hint: str) -> str:
    path = shutil.which(program)
    if not path:
        raise Failed(f"{program} is not installed: {hint}")
    return program


def run(
    cmd: list[str], env: dict | None = None, timeout: float = 900
) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env.update(env or {})
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, env=full_env, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise Failed(f"{cmd[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise Failed(f"{' '.join(cmd[:3])} did not finish in {int(timeout)}s") from exc


def run_ok(
    cmd: list[str], what: str, env: dict | None = None, timeout: float = 900
) -> str:
    proc = run(cmd, env, timeout)
    if proc.returncode != 0:
        raise Failed(
            f"{what} failed (exit {proc.returncode}): {redact((proc.stderr or proc.stdout).strip())[:400]}"
        )
    return proc.stdout


def run_json(
    cmd: list[str], what: str, env: dict | None = None, timeout: float = 900
) -> object:
    out = run_ok(cmd, what, env, timeout)
    try:
        return json.loads(out) if out.strip() else {}
    except ValueError as exc:
        raise Failed(f"{what}: the output is not JSON") from exc


def graph_cli(
    root: str, url: str, graph: dict, *args: str, timeout: float = 600
) -> tuple[int, object, str]:
    """`uv run --project <plugin> sdlc-graph <args>`; the URL travels in the environment only."""
    need(
        "uv", "install uv (https://docs.astral.sh/uv/) so the plugin can run sdlc-graph"
    )
    env = {"DATABASE_URL": url, graph["url_env"]: url, "CLAUDE_PROJECT_DIR": root}
    proc = run(
        ["uv", "run", "--project", cl.plugin_root(), "sdlc-graph", *args], env, timeout
    )
    try:
        parsed: object = json.loads(proc.stdout) if proc.stdout.strip() else None
    except ValueError:
        parsed = None
    return proc.returncode, parsed, redact(proc.stderr.strip())[-400:]


def ledger_status(root: str, url: str, graph: dict) -> dict:
    rc, out, err = graph_cli(root, url, graph, "status", "--json", timeout=120)
    if not isinstance(out, dict):
        return {
            "configured": False,
            "reachable": False,
            "error": err or f"sdlc-graph status exited {rc}",
        }
    return out


def db_init(root: str, url: str, graph: dict) -> dict:
    rc, out, err = graph_cli(root, url, graph, "db", "init", "--json")
    if rc != 0 or not isinstance(out, dict):
        raise Failed(f"sdlc-graph db init failed (exit {rc}): {err or redact(out)}")
    return out


# ---------- status, on, off, url ----------


def cmd_status(root: str, as_json: bool) -> int:
    data = read_config(root) if os.path.exists(config_path(root)) else {}
    graph, problems = graph_of(data)
    for p in problems:
        say(f"warning: {p}")
    if not graph["enabled"]:
        if as_json:
            say(
                json.dumps(
                    {
                        "enabled": False,
                        "configured": False,
                        "provider": graph["provider"],
                        "hint": OFF_LINE,
                    }
                )
            )
        else:
            say(OFF_LINE)
        return 0
    url, source = resolve_url(root, graph)
    st = ledger_status(root, url, graph)
    if as_json:
        say(json.dumps(st, indent=2, default=str))
        return 0 if st.get("configured") else 1
    provider = graph["provider"]
    say(f"graph: on, provider {provider}")
    say(f"url: {shown(url)}  (from {source})")
    if not st.get("reachable"):
        say(
            f"graph on but not reachable: run /factory:graph provision {provider} or set {graph['url_env']}"
        )
        if st.get("error"):
            say(f"  error: {st['error']}")
        return 1
    if not st.get("configured"):
        say(
            f"reachable, no ledger schema: run /factory:graph provision {provider} (it runs sdlc-graph db init)"
        )
        return 1
    say(
        f"reachable: yes, schema {st.get('schema_version')}, snapshots {st.get('snapshots')}, nodes {st.get('nodes')}"
    )
    pending = int(st.get("pending_migrations") or 0)
    if pending:
        say(
            f"behind by {pending} migration{'s' if pending != 1 else ''} (schema {st.get('schema_version')}, this plugin ships "
            f"{st.get('latest_version')}): run /factory:graph migrate (graph.py migrate; idempotent)"
        )
    if provider == "spanner":
        sp = graph.get("spanner") or {}
        say(
            f"spanner mirror: projects/{sp.get('project')}/instances/{sp.get('instance')}/databases/{sp.get('database')} "
            "(the ledger above stays on PostgreSQL)"
        )
    return 0


def cmd_migrate(root: str) -> int:
    """Apply the DDL files the database has not seen (sdlc-graph db init; every file is idempotent)."""
    graph, problems = graph_of(
        read_config(root) if os.path.exists(config_path(root)) else {}
    )
    if problems:
        raise Refused("; ".join(problems) + ": repair the project config first")
    if not graph["enabled"]:
        say(OFF_LINE)
        return 0
    url, _source = resolve_url(root, graph)
    before = ledger_status(root, url, graph)
    if not before.get("reachable"):
        say(
            f"graph on but not reachable: nothing migrated; run /factory:graph provision {graph['provider']} "
            f"or set {graph['url_env']}"
        )
        return 1
    if before.get("configured") and not before.get("pending_migrations"):
        say(f"schema {before.get('schema_version')} is current: nothing to migrate")
        return 0
    init = db_init(root, url, graph)
    after = ledger_status(root, url, graph)
    say(
        f"migrated: schema {before.get('schema_version') or 'empty'} -> {after.get('schema_version')} "
        f"({len(init.get('applied') or [])} DDL file(s) applied to schema {init.get('schema')}, idempotent)"
    )
    return 0 if after.get("configured") and not after.get("pending_migrations") else 1


def cmd_on_off(root: str, on: bool) -> int:
    graph, _ = graph_of(read_config(root))
    changed = write_graph(root, enabled=on)
    if on:
        say(
            ("graph on" if changed else "graph already on")
            + f" (provider {graph['provider']}): /factory:graph status says "
            "whether its database is reachable"
        )
    else:
        say(
            ("graph off" if changed else "graph already off")
            + ": every graph step is skipped and /factory:orchestrate "
            "refuses to start; hooks, contracts and evidence files stay"
        )
    return 0


def secret_from_aws(arn: str, region: str) -> str:
    need("aws", "install the AWS CLI v2 and sign in (aws sso login)")
    text = run_ok(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            arn,
            "--region",
            region,
            "--query",
            "SecretString",
            "--output",
            "text",
        ],
        "aws secretsmanager get-secret-value",
    ).strip()
    return url_from_secret(text)


def url_from_secret(
    text: str,
    host: str | None = None,
    port: int | None = None,
    dbname: str | None = None,
) -> str:
    """A secret is a URL, or RDS/Aurora JSON {username, password, host?, port?, dbname?}."""
    text = text.strip()
    if text.startswith(("postgres://", "postgresql://")):
        return keep_secret(text)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise Failed("the secret is neither a connection string nor RDS JSON") from exc
    if (
        not isinstance(data, dict)
        or not data.get("username")
        or not data.get("password")
    ):
        raise Failed("the secret has no username and password")
    if len(str(data["password"])) >= 8:
        SECRETS.add(str(data["password"]))
    h = host or data.get("host")
    if not h:
        raise Failed("the secret names no host; the endpoint is needed")
    p = port or data.get("port") or 5432
    d = dbname or data.get("dbname") or "sdlc"
    return keep_secret(
        f"postgresql://{quote(str(data['username']), safe='')}:{quote(str(data['password']), safe='')}"
        f"@{h}:{p}/{d}?sslmode=require"
    )


def cmd_url(root: str, opts: dict) -> int:
    graph, _ = graph_of(read_config(root))
    if opts.get("from_file"):
        rel = os.path.normpath(opts["from_file"])
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise Refused(f"cannot read {rel}: {exc.strerror}") from exc
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        url = check_url(text)
        how = f"from {rel.replace(os.sep, '/')} (deleted)"
    elif opts.get("from_aws_secret"):
        if not opts.get("region"):
            raise Refused("--from-aws-secret needs --region")
        url = check_url(secret_from_aws(opts["from_aws_secret"], opts["region"]))
        how = "from AWS Secrets Manager"
    elif opts.get("from_gcp_secret"):
        if not opts.get("project"):
            raise Refused("--from-gcp-secret needs --project")
        need("gcloud", "install the Google Cloud CLI and sign in (gcloud auth login)")
        text = run_ok(
            [
                "gcloud",
                "secrets",
                "versions",
                "access",
                "latest",
                "--secret",
                opts["from_gcp_secret"],
                "--project",
                opts["project"],
            ],
            "gcloud secrets versions access",
        )
        url = check_url(text)
        how = "from Google Secret Manager"
    else:
        text = (
            getpass.getpass("connection string (not shown): ")
            if sys.stdin.isatty()
            else sys.stdin.readline()
        )
        url = check_url(text)
        how = "from stdin"
    try:
        stored = store_url(root, graph["url_env"], url, opts.get("env_file"))
    except NotWritable as exc:
        raise Failed(
            f"{exc}: nothing was stored; a human runs graph.py url in their own terminal (it reads the "
            f"string at a hidden prompt)"
        ) from None
    say(stored + f"; {how}")
    say(f"stored for {shown(url)}: /factory:graph status checks it")
    return 0


# ---------- provision ----------


def finish(
    root: str,
    graph: dict,
    provider: str,
    url: str,
    where: str,
    env_file: str | None,
    extra: dict | None = None,
    store: bool = True,
) -> int:
    """db init, env file, status, evidence; prints the summary."""
    before = ledger_status(root, url, graph)
    init = db_init(root, url, graph)
    say(
        f"db init: {len(init.get('applied') or [])} DDL file(s) applied to schema {init.get('schema')} (idempotent)"
    )
    if store:
        try:
            say(store_url(root, graph["url_env"], url, env_file))
        except NotWritable as exc:
            resolved, source = resolve_url(root, graph)
            if (
                resolved != url
            ):  # nothing else leads to this database: without the stored copy it is lost
                raise Failed(
                    f"{exc}: the database is built but its URL is not stored; a human reruns this same "
                    f"provision command in their own terminal (it is idempotent)"
                ) from None
            # The URL's own source (the shell, or the local default) already leads here: only the convenience copy is
            # missing, so carry on with status and evidence and say so.
            say(
                f"not stored: {exc}. This session reaches the database through {source}; to keep the URL for "
                f"later sessions a human runs graph.py url in their own terminal (a hidden prompt), or sets "
                f"{graph['url_env']} in their shell"
            )
    after = ledger_status(root, url, graph)
    if not after.get("configured"):
        raise Failed(
            f"the database is still not usable after db init: {after.get('error') or after.get('hint')}"
        )
    schema = str(after.get("schema_version"))
    args = [
        "provisioned",
        "--provider",
        provider,
        "--where",
        where,
        "--schema",
        schema,
        "--repo-dir",
        root,
        "--json",
    ]
    if extra:
        args += ["--extra", json.dumps(extra)]
    rc, rec, err = graph_cli(root, url, graph, *args)
    if rc != 0 or not isinstance(rec, dict):
        raise Failed(f"recording graph_provisioned failed (exit {rc}): {err}")
    changed = write_graph(root, provider=provider)
    if changed:
        say(f"graph.provider set to {provider} in .claude/sdlc.json")
    there = rec.get("status") == "already_there" and before.get("configured")
    say(
        f"evidence: graph_provisioned {'already there' if rec.get('status') == 'already_there' else 'recorded'} "
        f"on {rec.get('slug')} (id {rec.get('evidence_id')})"
    )
    say(
        f"status: configured, provider {provider}, schema {schema}, snapshots {after.get('snapshots')}, nodes {after.get('nodes')}"
    )
    say(f"{'already there' if there else 'provisioned'}: {where}")
    return 0


COMPOSE_VARS = os.path.join(".sdlc-stage", "graph-compose.vars")


def compose_vars(root: str, port: int, project: str | None) -> str:
    """Write the variables docker-compose.yml interpolates (port, container, project name) to a file a top-level
    `docker compose --env-file` reads; returns the command line to run. Nothing secret is in the file."""
    lines = [f"SDLC_PG_PORT={port}"]
    if project:
        lines += [f"SDLC_PG_CONTAINER={project}-db", f"COMPOSE_PROJECT_NAME={project}"]
    path = os.path.join(root, COMPOSE_VARS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    added = gitignored(root, ".sdlc-stage/")
    if added:
        say(added)
    compose_file = os.path.join(cl.plugin_root(), "docker-compose.yml")
    return f'docker compose --env-file {COMPOSE_VARS} -f "{compose_file}" up -d db'


def wait_reachable(
    root: str,
    url: str,
    graph: dict,
    timeout: float,
    compose: list[str] | None = None,
    env: dict | None = None,
) -> bool:
    """Poll until a connection opens (and, with `compose`, pg_isready answers in the container)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if compose is not None:
            proc = run(compose, env, timeout=30)
            if proc.returncode != 0:
                time.sleep(1)
                continue
        if ledger_status(root, url, graph).get("reachable"):
            return True
        time.sleep(1)
    return False


def compose_cmd(project: str | None, *args: str) -> list[str]:
    cmd = ["docker", "compose"]
    if project:
        cmd += ["-p", project]
    return cmd + ["-f", os.path.join(cl.plugin_root(), "docker-compose.yml"), *args]


def provision_local(root: str, graph: dict, opts: dict) -> int:
    url, source = resolve_url(root, graph)
    parts = urlsplit(url)
    host, port = parts.hostname or "localhost", parts.port or 5432
    st = ledger_status(root, url, graph)
    project = opts.get("compose_project")
    extra: dict = {"compose_project": project or "sdlc-graph"}
    if st.get("reachable"):
        say(
            f"database reachable at {shown(url)} (from {source}): Docker not needed, db init only"
        )
        extra = {"docker": False}
    else:
        if host not in LOCAL_HOSTS and not host.endswith(".localhost"):
            raise Refused(
                f"the URL from {source} points at {host}, which is not local and not reachable: fix it, or "
                f"remove it to use the local default {DEFAULT_URL.split('@')[1]}"
            )
        timeout = float(opts.get("timeout") or 90)
        if opts.get("no_docker"):
            # The container was started by a top-level `docker compose` (the sandbox case): wait, never call Docker.
            if not wait_reachable(root, url, graph, timeout):
                raise Failed(
                    f"the database at {shown(url)} did not answer in {int(timeout)}s; start it first with "
                    f"`{compose_vars(root, port, project)}` (its own command), then rerun this one"
                )
            say(
                f"ready: a connection opened at {shown(url)} (Docker started outside this script)"
            )
        else:
            need(
                "docker",
                "install Docker Desktop (or Docker Engine with the compose plugin)",
            )
            env = {"SDLC_PG_PORT": str(port)}
            if project:
                env["SDLC_PG_CONTAINER"] = f"{project}-db"
            proc = run(compose_cmd(project, "up", "-d", "db"), env, timeout=600)
            if proc.returncode != 0:
                line = compose_vars(root, port, project)
                rerun = "provision local --no-docker" + (
                    f" --compose-project {project}" if project else ""
                )
                say(
                    f"docker compose up -d db failed (exit {proc.returncode}): "
                    f"{redact((proc.stderr or proc.stdout).strip())[:300]}"
                )
                say(
                    "In the pack's sandbox Docker runs only as its own top-level command (the sandbox excludes a "
                    "top-level docker compose; a child of this script cannot read ~/.docker). Run these two, one per call:"
                )
                say(f"next: {line}")
                say(f"next: graph.py {rerun}")
                raise Failed(
                    "docker compose up -d db did not run here; the two next: commands finish the job"
                )
            say(
                f"docker compose up -d db: {project or 'sdlc-graph'} on 127.0.0.1:{port}"
            )
            ready_cmd = compose_cmd(
                project, "exec", "-T", "db", "pg_isready", "-U", "sdlc", "-d", "sdlc"
            )
            if not wait_reachable(root, url, graph, timeout, ready_cmd, env):
                raise Failed(
                    f"the database did not become ready in {int(timeout)}s "
                    f"(docker compose -f <plugin root>/docker-compose.yml logs db)"
                )
            say("ready: pg_isready answered and a connection opened")
    rc = finish(
        root, graph, "local", url, url_where("local", url), opts.get("env_file"), extra
    )
    vars_file = os.path.join(root, COMPOSE_VARS)
    if os.path.exists(
        vars_file
    ):  # the top-level docker compose has read it; nothing is left behind
        os.remove(vars_file)
    return rc


def stored_and_reachable(root: str, graph: dict) -> tuple[str, str, dict] | None:
    """A URL someone stored (not the default) that answers: the database exists, nothing to create."""
    url, source = resolve_url(root, graph)
    if source == "default" or url == DEFAULT_URL:
        return None
    st = ledger_status(root, url, graph)
    return (url, source, st) if st.get("reachable") else None


def provision_neon(root: str, graph: dict, opts: dict) -> int:
    found = stored_and_reachable(root, graph)
    if found:
        url, source, _ = found
        say(f"a stored URL answers (from {source}): no Neon project is created")
        return finish(
            root,
            graph,
            "neon",
            url,
            url_where("neon", url),
            opts.get("env_file"),
            store=False,
        )
    need(
        "neonctl",
        "install it (npm i -g neonctl), or connect the Neon MCP: then the skill creates the project and "
        "stores the URL with url --from-file .sdlc-stage/graph-url",
    )
    if not os.environ.get("NEON_API_KEY"):
        raise Refused(
            "NEON_API_KEY is not set: export it in your own shell before starting the session "
            "(never paste it in chat), or use the Neon MCP"
        )
    SECRETS.add(os.environ["NEON_API_KEY"])
    name = opts.get("name") or f"{slug(repo_name(root))}-graph"
    listed = run_json(
        ["neonctl", "projects", "list", "--output", "json"], "neonctl projects list"
    )
    projects = (
        listed.get("projects", [])
        if isinstance(listed, dict)
        else listed
        if isinstance(listed, list)
        else []
    )
    match = next(
        (p for p in projects if isinstance(p, dict) and p.get("name") == name), None
    )
    if match:
        say(
            f"neon project {name} already there ({match.get('id')}, {match.get('region_id')})"
        )
    else:
        if not opts.get("region_id"):
            raise Refused(
                "the Neon region is a decision: pass --region-id (for example aws-us-east-2, aws-eu-central-1)"
            )
        made = run_json(
            [
                "neonctl",
                "projects",
                "create",
                "--name",
                name,
                "--region-id",
                opts["region_id"],
                "--output",
                "json",
            ],
            "neonctl projects create",
        )
        for uri in (
            (made.get("connection_uris") or []) if isinstance(made, dict) else []
        ):
            if isinstance(uri, dict) and uri.get("connection_uri"):
                keep_secret(uri["connection_uri"])
        match = made.get("project") if isinstance(made, dict) else None
        if not isinstance(match, dict) or not match.get("id"):
            raise Failed("neonctl projects create returned no project id")
        say(
            f"neon project {name} created ({match.get('id')}, {match.get('region_id')}, Postgres {match.get('pg_version')})"
        )
    url = check_url(
        run_ok(
            [
                "neonctl",
                "connection-string",
                "--project-id",
                str(match["id"]),
                "--database-name",
                "neondb",
            ],
            "neonctl connection-string",
        )
    )
    return finish(
        root,
        graph,
        "neon",
        url,
        url_where("neon", url),
        opts.get("env_file"),
        {"project_id": match.get("id"), "region": match.get("region_id")},
    )


def aws(*args: str, region: str) -> list[str]:
    return ["aws", *args, "--region", region, "--output", "json"]


def check_account(opts: dict) -> str:
    for key in ("region", "account"):
        if not opts.get(key):
            raise Refused(
                f"--{key} is a decision for aws (region, account and who pays): pass --{key}"
            )
    ident = run_json(
        ["aws", "sts", "get-caller-identity", "--output", "json"],
        "aws sts get-caller-identity",
    )
    account = str(ident.get("Account", "")) if isinstance(ident, dict) else ""
    if account != str(opts["account"]):
        raise Refused(
            f"the AWS credentials are for account {account or 'unknown'}, not {opts['account']}: "
            "switch profile (AWS_PROFILE) or pass the account you mean"
        )
    return account


def aws_instance(name: str, region: str) -> dict | None:
    proc = run(
        aws(
            "rds",
            "describe-db-instances",
            "--db-instance-identifier",
            name,
            region=region,
        )
    )
    if proc.returncode != 0:
        if "DBInstanceNotFound" in proc.stderr or "not found" in proc.stderr.lower():
            return None
        raise Failed(
            f"aws rds describe-db-instances failed: {redact(proc.stderr.strip())[:300]}"
        )
    rows = json.loads(proc.stdout or "{}").get("DBInstances") or []
    return rows[0] if rows else None


def provision_aws(root: str, graph: dict, opts: dict) -> int:
    shape = opts.get("shape")
    if shape not in ("aws-demo", "aws-private"):
        raise Refused(
            "the shape is a decision: --shape aws-demo (RDS, public to your IP, a pilot) or aws-private "
            "(Aurora Serverless v2 in private subnets, a CDK stack for your pipeline)"
        )
    found = stored_and_reachable(root, graph)
    if found:
        url, source, _ = found
        say(f"a stored URL answers (from {source}): nothing is created in AWS")
        return finish(
            root,
            graph,
            "aws",
            url,
            url_where("aws", url),
            opts.get("env_file"),
            {"shape": shape},
            store=False,
        )
    need("aws", "install the AWS CLI v2 and sign in (aws sso login)")
    account = check_account(opts)
    region = opts["region"]
    payer = opts.get("payer") or "unrecorded"
    if shape == "aws-private":
        return provision_aws_private(root, graph, opts, account, region, payer)
    if not opts.get("operator_cidr") or not re.fullmatch(
        r"\d{1,3}(\.\d{1,3}){3}/(3[0-2]|[12]?\d)", opts["operator_cidr"]
    ):
        raise Refused(
            "aws-demo opens port 5432 to one address only: pass --operator-cidr <your public IP>/32 "
            "(curl -s https://checkip.amazonaws.com prints the IP)"
        )
    name = opts.get("name") or slug(f"factory-graph-{repo_name(root)}", 63)
    inst = aws_instance(name, region)
    if inst:
        say(f"rds instance {name} already there ({inst.get('DBInstanceStatus')})")
    else:
        vpcs = (
            run_json(
                aws(
                    "ec2",
                    "describe-vpcs",
                    "--filters",
                    "Name=is-default,Values=true",
                    region=region,
                ),
                "aws ec2 describe-vpcs",
            ).get("Vpcs")
            or []
        )
        if not vpcs:
            raise Refused(
                f"no default VPC in {region}: use --shape aws-private, or create a default VPC first"
            )
        vpc = vpcs[0]["VpcId"]
        sg_name = f"{name}-sg"
        groups = (
            run_json(
                aws(
                    "ec2",
                    "describe-security-groups",
                    "--filters",
                    f"Name=group-name,Values={sg_name}",
                    f"Name=vpc-id,Values={vpc}",
                    region=region,
                ),
                "aws ec2 describe-security-groups",
            ).get("SecurityGroups")
            or []
        )
        if groups:
            sg = groups[0]["GroupId"]
        else:
            sg = run_json(
                aws(
                    "ec2",
                    "create-security-group",
                    "--group-name",
                    sg_name,
                    "--description",
                    "factory graph db: 5432 from the operator only",
                    "--vpc-id",
                    vpc,
                    region=region,
                ),
                "aws ec2 create-security-group",
            )["GroupId"]
            run_ok(
                aws(
                    "ec2",
                    "authorize-security-group-ingress",
                    "--group-id",
                    sg,
                    "--protocol",
                    "tcp",
                    "--port",
                    "5432",
                    "--cidr",
                    opts["operator_cidr"],
                    region=region,
                ),
                "aws ec2 authorize-security-group-ingress",
            )
        run_ok(
            aws(
                "rds",
                "create-db-instance",
                "--db-instance-identifier",
                name,
                "--engine",
                "postgres",
                "--engine-version",
                "16",
                "--db-instance-class",
                "db.t4g.micro",
                "--allocated-storage",
                "20",
                "--db-name",
                "sdlc",
                "--master-username",
                "sdlc",
                "--manage-master-user-password",
                "--publicly-accessible",
                "--vpc-security-group-ids",
                sg,
                "--storage-encrypted",
                "--backup-retention-period",
                "1",
                "--tags",
                "Key=created-by,Value=factory-graph",
                f"Key=payer,Value={slug(payer, 60)}",
                region=region,
            ),
            "aws rds create-db-instance",
        )
        say(
            f"rds instance {name} created (db.t4g.micro, PostgreSQL 16, {region}); waiting until it is available"
        )
    run_ok(
        [
            "aws",
            "rds",
            "wait",
            "db-instance-available",
            "--db-instance-identifier",
            name,
            "--region",
            region,
        ],
        "aws rds wait db-instance-available",
        timeout=1800,
    )
    inst = aws_instance(name, region) or {}
    endpoint = inst.get("Endpoint") or {}
    secret_arn = (inst.get("MasterUserSecret") or {}).get("SecretArn")
    if not endpoint.get("Address") or not secret_arn:
        raise Failed("the instance has no endpoint or managed secret yet")
    text = run_ok(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_arn,
            "--region",
            region,
            "--query",
            "SecretString",
            "--output",
            "text",
        ],
        "aws secretsmanager get-secret-value",
    )
    url = url_from_secret(
        text, endpoint["Address"], endpoint.get("Port") or 5432, "sdlc"
    )
    return finish(
        root,
        graph,
        "aws",
        url,
        url_where("aws", url),
        opts.get("env_file"),
        {
            "shape": "aws-demo",
            "account": account,
            "region": region,
            "payer": payer,
            "instance": name,
        },
    )


CDK_FILES: dict[str, str] = {
    "app.py": '''#!/usr/bin/env python3
"""The graph's database for /factory:graph provision aws --shape aws-private: Aurora PostgreSQL Serverless v2 in
private subnets of an existing VPC, credentials generated into Secrets Manager. Deploy it through the team's own
pipeline (cdk deploy from this directory); nothing here is deployed by the plugin."""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds


class GraphDbStack(cdk.Stack):
    def __init__(self, scope, construct_id, *, vpc_id, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
        sg = ec2.SecurityGroup(self, "GraphDbSg", vpc=vpc, allow_all_outbound=False,
                               description="factory graph db: 5432 from inside the VPC only")
        sg.add_ingress_rule(ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(5432), "runners and SSM hosts in the VPC")
        cluster = rds.DatabaseCluster(
            self, "GraphDb",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_16_4),
            credentials=rds.Credentials.from_generated_secret("sdlc"),
            default_database_name="sdlc",
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[sg],
            storage_encrypted=True,
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.SNAPSHOT,
            backup=rds.BackupProps(retention=cdk.Duration.days(7)),
        )
        cdk.Tags.of(self).add("created-by", "factory-graph")
        cdk.CfnOutput(self, "ClusterEndpoint", value=cluster.cluster_endpoint.hostname)
        cdk.CfnOutput(self, "ClusterPort", value=cdk.Token.as_string(cluster.cluster_endpoint.port))
        cdk.CfnOutput(self, "SecretArn", value=cluster.secret.secret_arn)


app = cdk.App()
ctx = app.node.try_get_context
GraphDbStack(app, "__STACK__", vpc_id=ctx("vpc_id"),
             env=cdk.Environment(account=ctx("account"), region=ctx("region")))
app.synth()
''',
    "cdk.json": """{
  "app": "python3 app.py",
  "context": {
    "account": "__ACCOUNT__",
    "region": "__REGION__",
    "vpc_id": "__VPC__",
    "payer": "__PAYER__"
  }
}
""",
    "requirements.txt": "aws-cdk-lib>=2.160.0,<3\nconstructs>=10,<11\n",
    "README.md": """# Graph database (aws-private)

Written by `/factory:graph provision aws --shape aws-private`. Aurora PostgreSQL Serverless v2 (0.5 to 2 ACU) in the
private subnets of `__VPC__` (`__REGION__`, account `__ACCOUNT__`, paid by `__PAYER__`), port 5432 open to the VPC's own
CIDR only, credentials generated into Secrets Manager, deletion protection on, snapshot on removal.

Deploy through the team's pipeline: `pip install -r requirements.txt`, then `cdk deploy` from this directory
(the account must be bootstrapped). Then run `/factory:graph provision aws --shape aws-private --region __REGION__
--account __ACCOUNT__` from a runner inside the VPC, or from a laptop through an SSM port forward to the cluster
endpoint with `--via-host localhost --via-port <local port>`: it reads the stack outputs, assembles the connection
string from the secret without printing it, runs `CREATE EXTENSION vector` and `sdlc-graph db init`, and records
the evidence. Private subnets without NAT: change `PRIVATE_WITH_EGRESS` to `PRIVATE_ISOLATED` in `app.py`.
""",
}


def write_cdk(root: str, account: str, region: str, vpc: str, payer: str) -> list[str]:
    out = []
    base = os.path.join(root, "infra", "graph-db")
    os.makedirs(base, exist_ok=True)
    for name, text in CDK_FILES.items():
        text = (
            text.replace("__STACK__", STACK_NAME)
            .replace("__ACCOUNT__", account)
            .replace("__REGION__", region)
            .replace("__VPC__", vpc)
            .replace("__PAYER__", payer)
        )
        path = os.path.join(base, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    out.append(f"= infra/graph-db/{name}")
                    continue
            path += ".new"
            out.append(f"! infra/graph-db/{name}.new (yours differs; compare)")
        else:
            out.append(f"+ infra/graph-db/{name}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return out


def provision_aws_private(
    root: str, graph: dict, opts: dict, account: str, region: str, payer: str
) -> int:
    proc = run(
        aws(
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            STACK_NAME,
            region=region,
        )
    )
    if proc.returncode != 0:
        if "does not exist" not in proc.stderr:
            raise Failed(
                f"aws cloudformation describe-stacks failed: {redact(proc.stderr.strip())[:300]}"
            )
        if not opts.get("vpc_id"):
            raise Refused(
                "aws-private needs the VPC: pass --vpc-id (its private subnets hold the cluster)"
            )
        for line in write_cdk(root, account, region, opts["vpc_id"], payer):
            say(line)
        say(
            f"pending: deploy infra/graph-db through your pipeline (stack {STACK_NAME}), then rerun this from a runner in "
            "the VPC or through an SSM port forward (--via-host localhost --via-port <port>)"
        )
        return 0
    stacks = json.loads(proc.stdout or "{}").get("Stacks") or [{}]
    outputs = {
        o.get("OutputKey"): o.get("OutputValue") for o in stacks[0].get("Outputs") or []
    }
    if not outputs.get("SecretArn") or not outputs.get("ClusterEndpoint"):
        raise Failed(
            f"stack {STACK_NAME} has no SecretArn or ClusterEndpoint output yet ({stacks[0].get('StackStatus')})"
        )
    text = run_ok(
        [
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            outputs["SecretArn"],
            "--region",
            region,
            "--query",
            "SecretString",
            "--output",
            "text",
        ],
        "aws secretsmanager get-secret-value",
    )
    host = opts.get("via_host") or outputs["ClusterEndpoint"]
    port = int(opts.get("via_port") or outputs.get("ClusterPort") or 5432)
    url = url_from_secret(text, host, port, "sdlc")
    where = (
        f"aws {outputs['ClusterEndpoint']}:{outputs.get('ClusterPort') or 5432}/sdlc"
    )
    return finish(
        root,
        graph,
        "aws",
        url,
        where,
        opts.get("env_file"),
        {
            "shape": "aws-private",
            "account": account,
            "region": region,
            "payer": payer,
            "stack": STACK_NAME,
        },
    )


def provision_spanner(root: str, graph: dict, opts: dict) -> int:
    sp = graph.get("spanner") or {}
    for key, flag in (
        ("project", "--project"),
        ("instance", "--instance"),
        ("database", "--database"),
    ):
        opts[key] = opts.get(key) or sp.get(key)
        if not opts[key]:
            raise Refused(
                f"{flag} is a decision for spanner (project, instance config and processing units too)"
            )
    for key, flag in (
        ("config", "--config"),
        ("processing_units", "--processing-units"),
    ):
        if not opts.get(key):
            raise Refused(
                f"{flag} is a decision for spanner (for example --config regional-us-central1 "
                "--processing-units 100)"
            )
    url, source = resolve_url(root, graph)
    ledger = ledger_status(root, url, graph)
    if not ledger.get("configured"):
        raise Refused(
            "the ledger runs on PostgreSQL with pgvector and Spanner only mirrors the graph: run "
            "/factory:graph provision local, neon or aws first, then provision spanner"
        )
    need(
        "gcloud",
        "install the Google Cloud CLI, then gcloud auth login and gcloud auth application-default login",
    )
    p, i, d = opts["project"], opts["instance"], opts["database"]
    created = []
    if (
        run(
            [
                "gcloud",
                "spanner",
                "instances",
                "describe",
                i,
                "--project",
                p,
                "--format",
                "json",
            ]
        ).returncode
        != 0
    ):
        run_ok(
            [
                "gcloud",
                "spanner",
                "instances",
                "create",
                i,
                "--project",
                p,
                "--config",
                opts["config"],
                "--processing-units",
                str(opts["processing_units"]),
                "--edition",
                "ENTERPRISE",
                "--description",
                "factory graph mirror",
                "--format",
                "json",
            ],
            "gcloud spanner instances create",
        )
        created.append("instance")
        say(
            f"spanner instance {i} created ({opts['config']}, {opts['processing_units']} processing units, Enterprise)"
        )
    else:
        say(f"spanner instance {i} already there")
    if (
        run(
            [
                "gcloud",
                "spanner",
                "databases",
                "describe",
                d,
                "--instance",
                i,
                "--project",
                p,
                "--format",
                "json",
            ]
        ).returncode
        != 0
    ):
        with tempfile.TemporaryDirectory() as tmp:
            ddl_path = os.path.join(tmp, "spanner.ddl")
            rc, _, err = graph_cli(
                root, url, graph, "spanner", "ddl", "--out", ddl_path, "--json"
            )
            if rc != 0 or not os.path.exists(ddl_path):
                raise Failed(f"sdlc-graph spanner ddl failed (exit {rc}): {err}")
            run_ok(
                [
                    "gcloud",
                    "spanner",
                    "databases",
                    "create",
                    d,
                    "--instance",
                    i,
                    "--project",
                    p,
                    "--ddl-file",
                    ddl_path,
                    "--format",
                    "json",
                ],
                "gcloud spanner databases create",
                timeout=1800,
            )
        created.append("database")
        say(
            f"spanner database {d} created from the ledger's DDL (sdlc-graph spanner ddl)"
        )
    else:
        say(f"spanner database {d} already there")
    rc, mirrored, err = graph_cli(
        root,
        url,
        graph,
        "spanner",
        "export",
        "--project",
        p,
        "--instance",
        i,
        "--database",
        d,
        "--json",
        timeout=1800,
    )
    if rc != 0 or not isinstance(mirrored, dict) or not mirrored.get("ok"):
        raise Failed(
            f"sdlc-graph spanner export failed (exit {rc}): {err or redact(mirrored)}"
        )
    rows = sum((mirrored.get("rows_loaded") or {}).values())
    say(f"mirror: {rows} row(s) loaded into projects/{p}/instances/{i}/databases/{d}")
    write_graph(root, spanner={"project": p, "instance": i, "database": d})
    where = f"spanner projects/{p}/instances/{i}/databases/{d}"
    args = [
        "provisioned",
        "--provider",
        "spanner",
        "--where",
        where,
        "--schema",
        str(ledger.get("schema_version")),
        "--repo-dir",
        root,
        "--json",
        "--extra",
        json.dumps(
            {
                "ledger": url_where(ledger.get("provider") or "postgres", url),
                "config": opts["config"],
                "processing_units": int(opts["processing_units"]),
            }
        ),
    ]
    rc, rec, err = graph_cli(root, url, graph, *args)
    if rc != 0 or not isinstance(rec, dict):
        raise Failed(f"recording graph_provisioned failed (exit {rc}): {err}")
    write_graph(root, provider="spanner")
    say(
        f"evidence: graph_provisioned {'already there' if rec.get('status') == 'already_there' else 'recorded'} "
        f"on {rec.get('slug')} (id {rec.get('evidence_id')})"
    )
    say(
        "the ledger stays on PostgreSQL; Spanner Graph answers GQL over the mirror (sdlc-graph spanner query)"
    )
    say(
        f"{'provisioned' if created else 'already there'}: {where}"
        + ("" if created else " (the mirror was refreshed)")
    )
    return 0


PROVIDERS = {
    "local": provision_local,
    "neon": provision_neon,
    "aws": provision_aws,
    "spanner": provision_spanner,
}
VALUE_FLAGS = {
    "--root": "root",
    "--env-file": "env_file",
    "--from-file": "from_file",
    "--from-aws-secret": "from_aws_secret",
    "--from-gcp-secret": "from_gcp_secret",
    "--region": "region",
    "--project": "project",
    "--compose-project": "compose_project",
    "--timeout": "timeout",
    "--region-id": "region_id",
    "--name": "name",
    "--shape": "shape",
    "--account": "account",
    "--payer": "payer",
    "--operator-cidr": "operator_cidr",
    "--vpc-id": "vpc_id",
    "--via-host": "via_host",
    "--via-port": "via_port",
    "--instance": "instance",
    "--database": "database",
    "--config": "config",
    "--processing-units": "processing_units",
}


def usage() -> str:
    return (__doc__ or "").split("Usage:", 1)[1].strip()


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: " + usage())
        return 0 if argv else 2
    opts: dict = {}
    words: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in VALUE_FLAGS:
            if i + 1 >= len(argv):
                print(f"{a} needs a value", file=sys.stderr)
                return 2
            opts[VALUE_FLAGS[a]] = argv[i + 1]
            i += 2
        elif a == "--json":
            opts["json"] = True
            i += 1
        elif a == "--no-docker":
            opts["no_docker"] = True
            i += 1
        elif (
            a == "--headless"
        ):  # the skill's own flag, passed through: the script never asks anything anyway
            i += 1
        elif a.startswith("-"):
            print(f"unknown argument {a}", file=sys.stderr)
            return 2
        else:
            words.append(a)
            i += 1
    root = cl.project_root(opts.get("root"))
    try:
        if words == ["status"]:
            return cmd_status(root, bool(opts.get("json")))
        if words == ["migrate"]:
            return cmd_migrate(root)
        if words in (["on"], ["off"]):
            return cmd_on_off(root, words[0] == "on")
        if words == ["url"]:
            return cmd_url(root, opts)
        if len(words) == 2 and words[0] == "provision" and words[1] in PROVIDERS:
            graph, problems = graph_of(read_config(root))
            if problems:
                raise Refused("; ".join(problems) + ": repair .claude/sdlc.json first")
            if not graph["enabled"]:
                raise Refused(f"{OFF_LINE}; run /factory:graph on before provisioning")
            say(f"provision {words[1]}: {root}")
            return PROVIDERS[words[1]](root, graph, opts)
        print("Usage: " + usage(), file=sys.stderr)
        return 2
    except Refused as exc:
        say(f"refused: {exc}")
        return 2
    except Failed as exc:
        say(f"failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
