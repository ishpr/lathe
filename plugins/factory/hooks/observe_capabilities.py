#!/usr/bin/env python3
"""PostToolUse observer: declared versus observed capabilities (DESIGN section 10.3, layer 3).

A contract may declare ``capabilities`` in .claude/work/current.json: ``paths`` (writable globs), ``commands`` (shell commands the
change needs) and ``egress`` (network domains). The sandbox and the edit/bash guards enforce what they can; this hook is the
third layer: it looks at what a tool call that already RAN actually touched and appends every access outside the declaration to
``.claude/work/<slug>/capability_findings.jsonl``. scripts/verify.py counts the lines (``capability_findings: N`` in the stamp) and
shows them to the reviewer. It never blocks and never prints: exit 0 always.

Observed, per call:
  Bash   network targets: curl / wget / http / xh / nc / ssh / scp / rsync URLs and hosts, git clone / fetch / pull / push / ls-remote /
         remote add (a remote name is resolved to its url), package installs (pip, uv, poetry, npm, pnpm, yarn, bun, cargo, go, gem,
         brew, apt) against the manager's registry hosts or an explicit index url; and file writes by redirection, tee, cp, mv,
         install, touch, mkdir, rm, ln, sed -i, dd of=.
  Edit / Write / MultiEdit   the file written.

A network access is declared when its host equals or is a subdomain of an ``egress`` domain, or when the command line starts with
a declared ``commands`` entry. A write is declared when it matches a ``paths`` glob; writes are compared only when ``paths`` is
present. ``.claude/work/**``, the temp directory and /dev are always fine. A contract with no (or an empty) ``capabilities`` declares
nothing, so nothing is compared. A record stores the tool, the kind (network | install | write), the target and the first word of
the command; never the command text (it can carry a token). Userinfo in a url is dropped. A repeated (kind, target) is written once.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import sdlc as cl  # noqa: E402

FINDINGS = "capability_findings.jsonl"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}
NET_COMMANDS = {"curl", "wget", "http", "https", "xh", "nc", "ncat", "ssh", "scp", "sftp", "rsync"}
WRAPPERS = {"sudo", "env", "time", "nohup", "command", "nice", "exec"}
SHELLS = {"bash", "sh", "zsh", "dash"}
TLDS = {"com", "org", "net", "io", "dev", "ai", "co", "app", "cloud", "gov", "edu", "us", "uk", "de", "sh", "xyz"}
OPERATORS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
SCP_RE = re.compile(r"^(?:[\w.-]+@)?([A-Za-z0-9.-]+\.[A-Za-z]{2,}|[\w-]+@[\w.-]+):")
BARE_HOST_RE = re.compile(r"^([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.([A-Za-z]{2,}))(?::\d+)?(?:/.*)?$")
REGISTRY_HOSTS = {
    "pip": ["pypi.org", "files.pythonhosted.org"],
    "npm": ["registry.npmjs.org"],
    "cargo": ["crates.io", "index.crates.io", "static.crates.io"],
    "go": ["proxy.golang.org"],
    "gem": ["rubygems.org"],
    "brew": ["ghcr.io", "formulae.brew.sh"],
    "apt": ["apt"],
}


def _words(text: str) -> list[str] | None:
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = "#"
        return list(lex)
    except ValueError:
        return None


def _segments(tokens: list[str]) -> list[list[str]]:
    out: list[list[str]] = [[]]
    for tok in tokens:
        if tok in OPERATORS:
            out.append([])
        else:
            out[-1].append(tok)
    return [s for s in out if s]


def _unwrap(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_]\w*=", w) or w in WRAPPERS or (w.startswith("-") and i > 0 and words[i - 1] in WRAPPERS):
            i += 1
        else:
            break
    return words[i:]


def _host_of_url(tok: str) -> str | None:
    try:
        return (urlsplit(tok).hostname or "").lower() or None
    except ValueError:
        return None


def _remote_url(root: str, name: str) -> str:
    try:
        proc = subprocess.run(["git", "-C", root, "config", "--get", f"remote.{name}.url"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _host_of_remote(tok: str) -> str | None:
    if SCHEME_RE.match(tok):
        return _host_of_url(tok)
    m = re.match(r"^(?:[\w.-]+@)?([A-Za-z0-9.-]+):(?!\d*/?$)", tok)  # git@github.com:org/repo.git, host:path
    return m.group(1).lower() if m and "/" not in tok.split(":", 1)[0] else None


def _installer(words: list[str]) -> str | None:
    """The registry family a package-install command talks to, or None."""
    if not words:
        return None
    name = os.path.basename(words[0])
    rest = words[1:]
    sub = next((w for w in rest if not w.startswith("-")), "")
    if name.startswith("python") and rest[:2] == ["-m", "pip"]:
        return "pip" if "install" in rest[2:] else None
    if name in ("pip", "pip3", "pipx"):
        return "pip" if sub in ("install", "download") else None
    if name == "uv":
        return "pip" if sub in ("add", "sync", "lock", "pip", "tool", "run") and (sub != "pip" or "install" in rest) else None
    if name == "poetry":
        return "pip" if sub in ("add", "install", "update") else None
    if name in ("npm", "pnpm", "yarn", "bun"):
        return "npm" if sub in ("install", "i", "add", "ci", "update", "up", "upgrade", "dlx", "create") else None
    if name in ("npx", "pnpx", "bunx"):
        return "npm"
    if name == "cargo":
        return "cargo" if sub in ("add", "install", "fetch", "update") else None
    if name == "go":
        return "go" if sub in ("get", "install") or rest[:2] == ["mod", "download"] else None
    if name == "gem":
        return "gem" if sub == "install" else None
    if name == "brew":
        return "brew" if sub in ("install", "upgrade", "reinstall") else None
    if name in ("apt", "apt-get", "apk", "yum", "dnf"):
        return "apt" if sub in ("install", "add", "update", "upgrade") else None
    return None


def accesses(words: list[str], root: str, depth: int = 0) -> list[tuple[str, str, list[str]]]:
    """``(kind, target, hosts)`` per observation of one simple command: kind network | install | write; for the first two
    ``hosts`` holds the hosts to compare (all must be declared), for a write the target is the path."""
    words = _unwrap(words)
    if not words:
        return []
    name = os.path.basename(words[0])
    rest = words[1:]
    found: list[tuple[str, str, list[str]]] = []

    if name in SHELLS and "-c" in rest and depth < 2 and rest.index("-c") + 1 < len(rest):
        inner = _words(rest[rest.index("-c") + 1]) or []
        for seg in _segments(inner):
            found += accesses(seg, root, depth + 1)
        return found

    urls = [h for t in rest if SCHEME_RE.match(t) and (h := _host_of_url(t))]
    family = _installer(words)
    if family:
        found.append(("install", f"{name} -> {(urls or REGISTRY_HOSTS[family])[0]}", urls or REGISTRY_HOSTS[family]))
    elif name == "git":
        sub = next((w for w in rest if not w.startswith("-")), "")
        args = rest[rest.index(sub) + 1:] if sub in rest else []
        hosts = [h for t in args if (h := _host_of_remote(t))]
        if sub in ("fetch", "pull", "push", "ls-remote") and not hosts:
            positional = [t for t in args if not t.startswith("-")]
            url = _remote_url(root, positional[0] if positional else "origin")
            h = _host_of_remote(url) if url else None
            hosts = [h] if h else []
        if sub in ("clone", "fetch", "pull", "push", "ls-remote", "remote", "submodule") and hosts:
            found.append(("network", f"git {sub} -> {hosts[0]}", hosts))
    elif name in NET_COMMANDS:
        hosts = list(urls)
        for t in rest:
            if t.startswith("-") or SCHEME_RE.match(t):
                continue
            if name in ("ssh", "scp", "sftp", "rsync"):
                m = SCP_RE.match(t) if name != "ssh" else None
                if m:
                    hosts.append(m.group(1).split("@")[-1].lower())
                elif name == "ssh" and not hosts and "/" not in t:
                    hosts.append(t.split("@")[-1].lower())
            else:
                m = BARE_HOST_RE.match(t)
                if m and m.group(2).lower() in TLDS:
                    hosts.append(m.group(1).lower())
        if hosts:
            found.append(("network", f"{name} -> {hosts[0]}", hosts))

    positional = [t for t in rest if not t.startswith("-")]
    written: list[str] = []
    if name == "tee":
        written = positional
    elif name in ("cp", "mv", "install", "ln") and len(positional) >= 2:
        written = [positional[-1]]
    elif name in ("touch", "mkdir", "rm", "rmdir", "truncate"):
        written = positional
    elif name == "sed" and any(t == "-i" or t.startswith("-i") and not t.startswith("--") for t in rest) and len(positional) >= 2:
        written = positional[1:]
    elif name == "dd":
        written = [t[3:] for t in rest if t.startswith("of=")]
    found += [("write", p, []) for p in written]
    for i, tok in enumerate(words[:-1]):
        if tok in REDIRECTS:
            found.append(("write", words[i + 1], []))
    return found


def host_declared(host: str, egress: list) -> bool:
    for d in egress:
        d = str(d).strip().lower().lstrip("*").lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


def command_declared(words: list[str], commands: list) -> bool:
    words = _unwrap(words)
    for c in commands:
        decl = _unwrap(_words(str(c).replace("*", "")) or [])
        if decl and words[:len(decl)] == decl:
            return True
    return False


def write_declared(rel: str, abs_path: str, paths: list) -> bool:
    if rel.startswith(".claude/work/") or abs_path.startswith("/dev/"):
        return True
    tmp = os.path.realpath(tempfile.gettempdir())
    if rel.startswith("..") and (os.path.realpath(abs_path).startswith(tmp + os.sep) or abs_path.startswith("/tmp/")):
        return True   # scratch outside the project (a project that itself lives under the temp directory is compared)
    return cl.matches(rel, [str(p) for p in paths], fold=True)


def observe(data: dict, root: str, caps: dict) -> list[dict]:
    """The out-of-declaration accesses of one tool call (no I/O beyond a git remote lookup)."""
    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") or {}
    egress, commands, paths = caps.get("egress") or [], caps.get("commands") or [], caps.get("paths")
    findings: list[dict] = []

    def rel_of(p: str) -> tuple[str, str]:
        absolute = p if os.path.isabs(p) else os.path.join(root, p)
        return cl.rel_to(root, os.path.normpath(os.path.expanduser(absolute))), os.path.normpath(os.path.expanduser(absolute))

    if tool in ("Edit", "Write", "MultiEdit"):
        p = tin.get("file_path") or ""
        if p and paths is not None:
            rel, absolute = rel_of(p)
            if not write_declared(rel, absolute, paths):
                findings.append({"tool": tool, "kind": "write", "target": rel, "command": tool.lower()})
        return findings
    if tool != "Bash" or not isinstance(tin.get("command"), str):
        return findings

    tokens = _words(tin["command"])
    if tokens is None:   # unparseable: still look for urls so a quoting trick does not hide a host
        tokens = re.findall(r"\S+", tin["command"])
    for seg in _segments(tokens):
        for kind, target, hosts in accesses(seg, root):
            verb = os.path.basename(_unwrap(seg)[0]) if _unwrap(seg) else ""
            if kind == "write":
                if paths is None or not target or target.startswith(("&", "-")) or target.isdigit():
                    continue
                rel, absolute = rel_of(target)
                if not write_declared(rel, absolute, paths):
                    findings.append({"tool": tool, "kind": "write", "target": rel, "command": verb})
            else:
                bad = [h for h in hosts if h not in LOCAL_HOSTS and not host_declared(h, egress)]
                if kind == "install" and len(bad) < len(hosts):
                    bad = []   # a registry has several hosts (pypi.org and files.pythonhosted.org): one declared is the declaration
                if bad and not command_declared(seg, commands):
                    findings.append({"tool": tool, "kind": kind, "target": target, "command": verb})
    return findings


def record(root: str, slug: str, findings: list[dict]) -> None:
    folder = os.path.join(root, ".claude", "work", slug)
    path = os.path.join(folder, FINDINGS)
    seen: set[tuple] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    old = json.loads(line)
                    seen.add((old.get("kind"), old.get("target")))
                except ValueError:
                    pass
    except OSError:
        pass
    fresh = []
    for f in findings:
        if (f["kind"], f["target"]) not in seen:
            seen.add((f["kind"], f["target"]))
            fresh.append({"ts": int(time.time()), **f})
    if fresh:
        os.makedirs(folder, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for f in fresh:
                fh.write(json.dumps(f) + "\n")


def main() -> int:
    try:
        data = cl.read_hook_input()
        if not isinstance(data, dict) or not isinstance(data.get("tool_input") or {}, dict):
            return 0
        root = cl.project_root(data.get("cwd"))
        contract = cl.load_contract(root)
        caps = contract.get("capabilities") if isinstance(contract, dict) else None
        slug = contract.get("slug") if isinstance(contract, dict) else None
        if not isinstance(caps, dict) or not caps or not isinstance(slug, str) or not re.match(r"^[\w.-]+$", slug):
            return 0
        findings = observe(data, root, caps)
        if findings:
            record(root, slug, findings)
    except Exception:  # noqa: BLE001 - an observer must never break a tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
