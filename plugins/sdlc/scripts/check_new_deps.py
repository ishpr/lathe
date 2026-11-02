#!/usr/bin/env python3
"""Check every newly added dependency before it lands (REC-24: unpinned deps, slopsquatting).

Compares dependency manifests at --base and --head (via `git show`): requirements*.txt and *.in,
pyproject.toml ([project] dependencies and optional-dependencies, PEP 735 [dependency-groups],
[build-system] requires, [tool.poetry] dependencies and groups), package.json
dependencies/devDependencies, go.mod require, Cargo.toml [dependencies].
Direct references (`git+https://...`, a URL or wheel link, `-e git+...`, `name @ url`, an npm
git/http spec, a cargo `git =` dependency) bypass the registry, which is exactly where a squatted or
hallucinated package hides: each is reported as DIRECT-REF "needs human review" and exits 1 (also
with --offline). Local paths (`./x`, `file:`, `path =`) are not remote and are skipped. Lists packages added since the base. Unless --offline, each one is
looked up (urllib, 10 s timeout) on PyPI JSON, registry.npmjs.org, proxy.golang.org or crates.io and
reported as:
  MISSING   the registry has no such package (a hallucinated or squatted name)  -> exit 1
  YOUNG     first release is newer than min_package_age_days (config, default 30) -> exit 1
  NO-LICENCE the registry shows no licence                                       -> exit 1
  UNCHECKED network error, so nothing was verified ("could not check")           -> exit 1
  ok        exists, old enough, licence known
Go: the module proxy carries no licence data, so the licence column reads "n/a" and does not fail.
--offline lists the added packages and exits 0.

Exit codes: 0 ok (or nothing added, or --offline), 1 problems, 2 misuse or git failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

TIMEOUT = 10
UA = "sdlc-check-new-deps/1.0 (+stdlib urllib)"
SKIP_PARTS = ("node_modules/", "vendor/", ".venv/", "venv/", "site-packages/")


def git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)


def manifests(root: str, ref: str) -> list[str]:
    res = git(root, "ls-tree", "-r", "--name-only", ref)
    if res.returncode != 0:
        raise RuntimeError("git ls-tree %s failed: %s" % (ref, res.stderr.strip()))
    out = []
    for p in res.stdout.splitlines():
        b = os.path.basename(p)
        if any(s in p for s in SKIP_PARTS):
            continue
        if (re.fullmatch(r"requirements[\w.-]*\.(?:txt|in)", b) or b in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml")):
            out.append(p)
    return out


def show(root: str, ref: str, path: str) -> str:
    res = git(root, "show", "%s:%s" % (ref, path))
    return res.stdout if res.returncode == 0 else ""


def norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


DIRECT_PREFIX = ("git+", "hg+", "svn+", "bzr+", "http://", "https://", "ssh://", "git://")


def parse_requirements(text: str) -> set[tuple[str, str]]:
    out = set()
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-e ", "--editable ", "--editable=")):  # `-e git+...` is still a remote fetch
            line = re.sub(r"^(?:-e|--editable)[=\s]+", "", line).strip()
        elif line.startswith("-"):  # -r, -c, --index-url, ...: not a package
            continue
        if line.startswith(DIRECT_PREFIX):
            out.add(("direct", line))
            continue
        if line.startswith((".", "/", "file:")):
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*@\s*(\S+)", line)  # PEP 508 `name @ url`
        if m:
            if not m.group(2).startswith(("file:", ".", "/")):
                out.add(("direct", line))
            continue
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            out.add(("pypi", norm_py(m.group(1))))
    return out


def parse_pyproject(text: str) -> set[tuple[str, str]]:
    if not text or tomllib is None:
        return set()
    try:
        proj = tomllib.loads(text).get("project", {})
    except Exception:  # noqa: BLE001
        return set()
    try:
        return _parse_pyproject_data(text, proj)
    except Exception:  # noqa: BLE001
        return set()


def _parse_pyproject_data(text: str, proj: dict) -> set[tuple[str, str]]:
    data = tomllib.loads(text)
    reqs = list(proj.get("dependencies", []))
    for group in proj.get("optional-dependencies", {}).values():
        reqs += group
    for group in (data.get("dependency-groups") or {}).values():  # PEP 735; {include-group = ...} items are tables
        reqs += [r for r in group if isinstance(r, str)]
    reqs += list((data.get("build-system") or {}).get("requires") or [])
    out = parse_requirements("\n".join(r for r in reqs if isinstance(r, str)))
    poetry = (data.get("tool") or {}).get("poetry") or {}
    tables = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
    tables += [(g or {}).get("dependencies") for g in (poetry.get("group") or {}).values()]
    for table in tables:
        for name, spec in (table or {}).items():
            if name.lower() == "python":
                continue
            specs = spec if isinstance(spec, list) else [spec]
            for sp in specs:
                if isinstance(sp, dict) and ("git" in sp or "url" in sp):
                    out.add(("direct", "%s @ %s" % (name, sp.get("git") or sp.get("url"))))
                elif isinstance(sp, dict) and "path" in sp:
                    continue
                else:
                    out.add(("pypi", norm_py(name)))
    return out


def parse_package_json(text: str) -> set[tuple[str, str]]:
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return set()
    out = set()
    for sec in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (data.get(sec) or {}).items():
            if isinstance(spec, str) and re.match(r"(file:|link:|workspace:|\.|/)", spec):
                continue
            if isinstance(spec, str) and re.match(r"(git|http|github:|gitlab:|bitbucket:|[\w.-]+/[\w.-]+(?:#|$))", spec):
                out.add(("direct", "%s -> %s" % (name, spec)))  # git/URL/GitHub-shorthand spec: not the registry
                continue
            out.add(("npm", name))
    return out


def parse_go_mod(text: str) -> set[tuple[str, str]]:
    out, block = set(), False
    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        if line.startswith("require ("):
            block = True
        elif block and line == ")":
            block = False
        elif block and line:
            out.add(("go", line.split()[0]))
        elif line.startswith("require ") and "(" not in line:
            parts = line.split()
            if len(parts) >= 2:
                out.add(("go", parts[1]))
    return out


def parse_cargo(text: str) -> set[tuple[str, str]]:
    if not text or tomllib is None:
        return set()
    try:
        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, spec in (data.get(sec) or {}).items():
            if isinstance(spec, dict):
                if "git" in spec:
                    out.add(("direct", "%s -> git %s" % (name, spec["git"])))
                    continue
                if "path" in spec or spec.get("workspace"):
                    continue
                name = spec.get("package", name)
            out.add(("crates", name))
    return out


def parse_manifest(path: str, text: str) -> set[tuple[str, str]]:
    b = os.path.basename(path)
    if b.startswith("requirements"):
        return parse_requirements(text)
    return {"pyproject.toml": parse_pyproject, "package.json": parse_package_json,
            "go.mod": parse_go_mod, "Cargo.toml": parse_cargo}[b](text)


def collect(root: str, ref: str) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for path in manifests(root, ref):
        for key in parse_manifest(path, show(root, ref, path)):
            found.setdefault(key, path)
    return found


# ---------- registry lookups ----------

class NetError(Exception):
    pass


def fetch_json(url: str):
    """Return parsed JSON, or None for 404/410. Raise NetError for anything else."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        raise NetError("HTTP %d from %s" % (e.code, url))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise NetError("%s: %s" % (url, getattr(e, "reason", e)))


def fetch_text(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        raise NetError("HTTP %d from %s" % (e.code, url))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetError("%s: %s" % (url, getattr(e, "reason", e)))


def parse_ts(s: str) -> datetime | None:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def lookup_pypi(name: str) -> dict | None:
    d = fetch_json("https://pypi.org/pypi/%s/json" % urllib.parse.quote(name))
    if d is None:
        return None
    times = [t for files in (d.get("releases") or {}).values() for f in files
             if (t := parse_ts(f.get("upload_time_iso_8601", "")))]
    info = d.get("info") or {}
    lic = ""
    for c in info.get("classifiers") or []:
        if c.startswith("License ::"):
            lic = c.split("::")[-1].strip()
    lic = lic or (info.get("license_expression") or "").strip()
    raw = (info.get("license") or "").strip()
    if not lic and raw:
        lic = raw if len(raw) <= 60 else raw.splitlines()[0][:40] + " (text)"
    return {"first": min(times) if times else None, "licence": lic}


def lookup_npm(name: str) -> dict | None:
    d = fetch_json("https://registry.npmjs.org/%s" % urllib.parse.quote(name, safe="@").replace("/", "%2f"))
    if d is None:
        return None
    latest = (d.get("dist-tags") or {}).get("latest")
    ver = (d.get("versions") or {}).get(latest, {}) if latest else {}
    lic = ver.get("license") or d.get("license") or ""
    if isinstance(lic, dict):
        lic = lic.get("type", "")
    return {"first": parse_ts((d.get("time") or {}).get("created", "")), "licence": str(lic)}


def go_escape(mod: str) -> str:
    return "".join("!" + c.lower() if c.isupper() else c for c in mod)


def lookup_go(name: str) -> dict | None:
    base = "https://proxy.golang.org/%s/@v" % go_escape(name)
    lst = fetch_text(base + "/list")
    if lst is None:
        return None
    versions = [v for v in lst.split() if v]
    first = None
    if versions:
        def key(v: str):
            return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v.lstrip("v"))]
        info = fetch_json("%s/%s.info" % (base, urllib.parse.quote(sorted(versions, key=key)[0])))
        first = parse_ts((info or {}).get("Time", ""))
    else:
        info = fetch_json("%s/latest" % base)
        if info is None:
            return None
        first = parse_ts(info.get("Time", ""))
    return {"first": first, "licence": "n/a"}


def lookup_crates(name: str) -> dict | None:
    d = fetch_json("https://crates.io/api/v1/crates/%s" % urllib.parse.quote(name))
    if d is None:
        return None
    versions = d.get("versions") or []
    return {"first": parse_ts((d.get("crate") or {}).get("created_at", "")),
            "licence": (versions[0].get("license") if versions else "") or ""}


LOOKUP = {"pypi": lookup_pypi, "npm": lookup_npm, "go": lookup_go, "crates": lookup_crates}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="List dependencies added since --base and check each exists, is old enough and has a licence.",
        epilog="Exit: 0 ok/offline, 1 problems (missing, young, no licence, could not check), 2 misuse.",
    )
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--base", required=True, help="base ref, e.g. origin/main")
    ap.add_argument("--head", default="HEAD", help="head ref (default HEAD)")
    ap.add_argument("--offline", action="store_true", help="only list added packages; no network")
    ap.add_argument("--min-age-days", type=int, help="override min_package_age_days from .claude/sdlc.json (default 30)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    root = os.path.abspath(args.root)
    min_age = args.min_age_days
    if min_age is None:
        min_age = 30
        try:
            with open(os.path.join(root, ".claude", "sdlc.json"), encoding="utf-8") as fh:
                min_age = int(json.load(fh).get("min_package_age_days", 30))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    try:
        before, after = collect(root, args.base), collect(root, args.head)
    except RuntimeError as exc:
        print("check_new_deps: %s" % exc, file=sys.stderr)
        return 2
    added = sorted(k for k in after if k not in before)
    if not added:
        print("check_new_deps: no new packages between %s and %s" % (args.base, args.head))
        return 0
    print("check_new_deps: %d new package(s) between %s and %s" % (len(added), args.base, args.head))
    rows, problems, direct = [], 0, 0
    now = datetime.now(timezone.utc)
    for eco, name in added:
        where = after[(eco, name)]
        if eco == "direct":
            problems += 1
            direct += 1
            rows.append((eco, name, where, "-", "-", "DIRECT-REF: direct reference (not on a registry); needs human review"))
            continue
        if args.offline:
            rows.append((eco, name, where, "-", "-", "not checked (--offline)"))
            continue
        try:
            meta = LOOKUP[eco](name)
        except NetError as exc:
            problems += 1
            rows.append((eco, name, where, "?", "?", "UNCHECKED: could not check (%s)" % exc))
            continue
        if meta is None:
            problems += 1
            rows.append((eco, name, where, "-", "-", "MISSING: not found on the registry"))
            continue
        age = (now - meta["first"]).days if meta["first"] else None
        lic = meta["licence"]
        status = []
        if age is None:
            problems += 1
            status.append("UNCHECKED: no release date")
        elif age < min_age:
            problems += 1
            status.append("YOUNG: first release %d day(s) ago, minimum %d" % (age, min_age))
        if not lic:
            problems += 1
            status.append("NO-LICENCE: licence unknown")
        rows.append((eco, name, where, "%dd" % age if age is not None else "?", lic or "unknown", "; ".join(status) or "ok"))
    hdr = ("ecosystem", "package", "manifest", "age", "licence", "result")
    widths = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(5)]
    for r in [hdr] + rows:
        print("  ".join(str(r[i]).ljust(widths[i]) for i in range(5)) + "  " + r[5])
    if args.offline:
        print("check_new_deps: --offline, nothing verified against a registry")
        if direct:
            print("check_new_deps: %d direct reference(s) need human review" % direct)
            return 1
        return 0
    if problems:
        print("check_new_deps: %d problem(s); fix them or have a human review the new package(s)" % problems)
        return 1
    print("check_new_deps: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print("check_new_deps: crashed: %r" % (exc,), file=sys.stderr)
        sys.exit(2)
