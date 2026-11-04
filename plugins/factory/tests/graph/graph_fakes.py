"""A small, deterministic stand-in for the real extractors, used by test_snapshot / test_queries so
the graph store is tested independently of the extractors' state. It understands just enough Python
(top-level defs and classes, ``from x import y``, calls by name, ``@router.get`` routes, pytest
functions) and TypeScript (``fetch('/x')``) to build the synthetic repo's graph."""

from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path

NAME = "fake_extract_for_tests"


def _qual(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _sym(kind, qualname, path, name=None, line=None, **attrs):
    return {"kind": kind, "name": name or qualname.rsplit(".", 1)[-1], "qualname": qualname, "path": path,
            "line_start": line, "line_end": line, "attrs": attrs}


def _key(s):
    return {"kind": s["kind"], "qualname": s["qualname"], "path": s["path"]}


def extract(root: Path) -> dict:
    files = {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*.py"))}
    mods = {_qual(rel): rel for rel in files}
    trees = {rel: ast.parse(p.read_text()) for rel, p in files.items()}
    included: set[str] = set()
    for t in trees.values():
        for n in ast.walk(t):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "include_router"
                    and n.args and isinstance(n.args[0], ast.Attribute) and isinstance(n.args[0].value, ast.Name)):
                included.add(n.args[0].value.id)
    symbols, edges, tops = [], [], {}
    for rel, tree in trees.items():
        mq = _qual(rel)
        msym = _sym("module", mq, rel, name=mq.rsplit(".", 1)[-1], line=1, lang="python")
        symbols.append(msym)
        prefix = ""
        for n in tree.body:
            if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and getattr(n.value.func, "id", "") == "APIRouter"):
                for kw in n.value.keywords:
                    if kw.arg == "prefix":
                        prefix = kw.value.value
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                s = _sym("class", f"{mq}.{n.name}", rel, line=n.lineno)
                symbols.append(s)
                tops[(mq, n.name)] = s
                edges.append({"src": _key(msym), "dst": _key(s), "rel": "defines", "attrs": {}})
                if any(isinstance(b, ast.Assign) and getattr(b.targets[0], "id", "") == "__tablename__" for b in n.body):
                    m = _sym("model", f"{mq}.{n.name}", rel, line=n.lineno, table="items")
                    symbols.append(m)
                    edges.append({"src": _key(s), "dst": _key(m), "rel": "defines", "attrs": {}})
            elif isinstance(n, ast.FunctionDef):
                is_test = rel.startswith("tests/") and n.name.startswith("test_")
                s = _sym("test" if is_test else "function", f"{mq}.{n.name}", rel, line=n.lineno,
                         **({"nodeid": f"{rel}::{n.name}"} if is_test else {}))
                symbols.append(s)
                tops[(mq, n.name)] = s
                edges.append({"src": _key(msym), "dst": _key(s), "rel": "defines", "attrs": {}})
                for d in n.decorator_list:
                    if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and d.func.attr in ("get", "post", "delete") and d.args):
                        owner = d.func.value.id
                        pre = prefix if owner == "router" else ""
                        route = _sym("route", f"{d.func.attr.upper()} {pre}{d.args[0].value}", rel, name=n.name,
                                     line=n.lineno, mounted=owner != "router" or Path(rel).stem in included)
                        symbols.append(route)
                        edges.append({"src": _key(s), "dst": _key(route), "rel": "exposes_route", "attrs": {}})
    by_key = {(s["kind"], s["qualname"], s["path"]): s for s in symbols}
    for rel, tree in trees.items():
        mq = _qual(rel)
        names: dict[str, dict] = {}
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.module:
                if n.module in mods:
                    edges.append({"src": _key(by_key[("module", mq, rel)]),
                                  "dst": _key(by_key[("module", n.module, mods[n.module])]), "rel": "imports", "attrs": {}})
                for a in n.names:
                    sub = f"{n.module}.{a.name}"
                    if sub in mods:
                        edges.append({"src": _key(by_key[("module", mq, rel)]),
                                      "dst": _key(by_key[("module", sub, mods[sub])]), "rel": "imports", "attrs": {}})
                    elif (n.module, a.name) in tops:
                        names[a.asname or a.name] = tops[(n.module, a.name)]
        for n in tree.body:
            if not isinstance(n, ast.FunctionDef):
                continue
            src = tops[(mq, n.name)]
            rel_name = "tests" if src["kind"] == "test" else "calls"
            for c in ast.walk(n):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name):
                    dst = names.get(c.func.id) or tops.get((mq, c.func.id))
                    if dst is not None and dst is not src:
                        edges.append({"src": _key(src), "dst": _key(dst), "rel": rel_name, "attrs": {}})
    for p in sorted(root.rglob("*.ts")):
        rel = p.relative_to(root).as_posix()
        for i, m in enumerate(re.finditer(r"fetch\('([^']+)'\)", p.read_text())):
            symbols.append(_sym("frontend_call", f"GET {m.group(1)}", rel, line=i + 1, url=m.group(1)))
    return {"symbols": symbols, "edges": edges}


def link(merged: dict) -> list:
    routes = {s["qualname"]: s for s in merged["symbols"] if s["kind"] == "route"}
    return [{"src": _key(s), "dst": _key(routes[s["qualname"]]), "rel": "fetches", "attrs": {}}
            for s in merged["symbols"] if s["kind"] == "frontend_call" and s["qualname"] in routes]


def parse_owners(root: Path) -> list[dict]:
    path = Path(root) / "CODEOWNERS"
    if not path.exists():
        return []
    rules = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            glob, *handles = line.split()
            rules.append({"path_glob": glob, "handles": handles, "ord": len(rules)})
    return rules


def install(monkeypatch) -> None:
    """Route snapshot.extract_all through this fake extractor and CODEOWNERS parsing through ``parse_owners``."""
    from sdlc_graph.codegraph import snapshot

    module = types.ModuleType(NAME)
    module.extract, module.link = extract, link
    monkeypatch.setitem(sys.modules, NAME, module)
    monkeypatch.setattr("sdlc_graph.codegraph.EXTRACTORS", (NAME,))
    monkeypatch.setattr(snapshot, "_owner_rules", parse_owners)


def _emulator_running() -> str | None:
    """Skip reason unless a Spanner emulator is ALREADY listening (``make spanner-up``); None when one is. The export and
    parity tests load into a standing emulator and never start one, so a pullable image is not enough. Shared by
    test_spanner.py and test_lifecycle_parity.py."""
    from sdlc_graph.spanner import emulator_smoke

    host = emulator_smoke.emulator_host()
    if emulator_smoke._reachable(host):
        return None
    return f"no Spanner emulator listening at {host}: run 'make spanner-up' to include this test"


def graph_conftest():
    """tests/graph/conftest.py as its own module (``graph_conftest``). A test that needs its helpers imports them from
    here, not ``from conftest import``: when tests/sdlc and tests/graph run in one pytest process, the name ``conftest``
    belongs to whichever suite loaded first (lathe's own gate runs both; finding #55)."""
    import importlib.util
    import sys
    from pathlib import Path

    mod = sys.modules.get("graph_conftest")
    if mod is None:
        spec = importlib.util.spec_from_file_location("graph_conftest", Path(__file__).with_name("conftest.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["graph_conftest"] = mod
        spec.loader.exec_module(mod)
    return mod
