"""Dependency extractor, owners parser and the depends_on_package link."""

from __future__ import annotations

from pathlib import Path

from sdlc_graph.codegraph import extract_deps as ed
from sdlc_graph.codegraph import extract_frontend as fe
from sdlc_graph.codegraph import extract_owners as eo
from sdlc_graph.codegraph import extract_python as ep


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _pkgs(result):
    return {(s["qualname"], s["path"]): s["attrs"] for s in result["symbols"] if s["kind"] == "package"}


def test_normalise():
    assert ed.normalise("SQLAlchemy") == "sqlalchemy"
    assert ed.normalise("python_dotenv") == "python-dotenv"
    assert ed.normalise("Zope.Interface") == "zope-interface"


def test_synthetic_packages(synthetic_repo, synthetic_facts):
    pk = _pkgs(ed.extract(synthetic_repo))
    assert sorted(q for q, _ in pk) == synthetic_facts["packages"]
    assert pk[("fastapi", "requirements.txt")]["spec"] == "fastapi==0.115.0"
    assert pk[("sqlalchemy", "requirements.txt")]["manifest_kind"] == "requirements"


def test_manifest_forms(tmp_path):
    _write(tmp_path, {
        "requirements.txt": "-r base.txt\n# comment\nrequests[security]>=2 ; python_version>'3.8'  # trailing\n"
                            "git+https://github.com/x/y.git#egg=Some_Pkg\n-e .\n--index-url https://x\nhttps://h/z.whl\n",
        "requirements-dev.txt": "pytest\n",
        "node_modules/skip/requirements.txt": "ignored\n",
        "pyproject.toml": '[project]\ndependencies = ["fastapi", "Celery>=5"]\n'
                          '[project.optional-dependencies]\ndocs = ["mkdocs"]\n',
        "frontend/package.json": '{"dependencies": {"react": "^18"}, "devDependencies": {"@types/node": "^20"}}',
    })
    pk = _pkgs(ed.extract(tmp_path))
    assert pk[("requests", "requirements.txt")]["extras"] == ["security"]
    assert pk[("some-pkg", "requirements.txt")]["confidence"] == "low"
    assert ("ignored", "node_modules/skip/requirements.txt") not in pk
    assert pk[("pytest", "requirements-dev.txt")]["group"] == "dev"
    assert pk[("mkdocs", "pyproject.toml")]["group"] == "docs"
    assert pk[("celery", "pyproject.toml")]["group"] == "main"
    assert pk[("react", "frontend/package.json")]["manifest_kind"] == "package.json"
    assert pk[("@types/node", "frontend/package.json")]["group"] == "dev"
    assert not [q for q, _ in pk if q in {"base", "-e", "."}]


def test_unparseable_manifests_do_not_raise(tmp_path):
    _write(tmp_path, {"pyproject.toml": "this is [not toml", "package.json": "{nope", "requirements.txt": "\n\n"})
    assert ed.extract(tmp_path) == {"symbols": [], "edges": []}


def test_link_depends_on_package(synthetic_repo):
    py = ep.extract(synthetic_repo)
    merged = {"symbols": py["symbols"] + ed.extract(synthetic_repo)["symbols"], "edges": []}
    got = {(e["src"]["qualname"], e["dst"]["qualname"]) for e in ed.link(merged)}
    assert ("app.main", "fastapi") in got and ("app.models", "sqlalchemy") in got
    assert ("tests.test_a", "fastapi") not in got
    assert all(e["rel"] == "depends_on_package" for e in ed.link(merged))


def test_link_alias_and_prefix_confidence_and_frontend(tmp_path):
    _write(tmp_path, {
        "app/__init__.py": "",
        "app/a.py": "import yaml\nimport psycopg2\nimport google.cloud\n",
        "requirements.txt": "PyYAML\npsycopg2-binary\nprotobuf\n",
        "frontend/package.json": '{"dependencies": {"react": "1"}}',
        "frontend/src/x.tsx": "import React from 'react';\nimport { y } from './y';\n",
    })
    py, fx, dp = ep.extract(tmp_path), fe.extract(tmp_path), ed.extract(tmp_path)
    merged = {"symbols": py["symbols"] + fx["symbols"] + dp["symbols"], "edges": []}
    edges = {(e["src"]["qualname"], e["dst"]["qualname"]): e["attrs"] for e in ed.link(merged)}
    assert ("app.a", "pyyaml") in edges and ("app.a", "psycopg2-binary") in edges
    assert ("frontend/src/x.tsx", "react") in edges


def test_sample_app_deps(sample_app):
    pk = _pkgs(ed.extract(sample_app))
    assert {"fastapi", "sqlalchemy", "uvicorn", "httpx", "pytest"} <= {q for q, p in pk if p == "pyproject.toml"}
    py = ep.extract(sample_app)
    merged = {"symbols": py["symbols"] + ed.extract(sample_app)["symbols"], "edges": []}
    got = {(e["src"]["qualname"], e["dst"]["qualname"]) for e in ed.link(merged)}
    assert {("app.main", "fastapi"), ("app.db", "sqlalchemy"), ("tests.conftest", "pytest")} <= got


# --------------------------------------------------------------------------- owners


def test_synthetic_owners(synthetic_repo, synthetic_facts):
    rules = eo.parse(synthetic_repo)
    assert [r["path_glob"] for r in rules] == ["*", "/app/routes/", "/frontend/"]
    assert [r["ord"] for r in rules] == [0, 1, 2]
    for path, handles in synthetic_facts["owners"].items():
        assert eo.match(path, rules) == handles, path


def _rules(text: str):
    return [{"path_glob": g.split()[0], "handles": g.split()[1:], "ord": i} for i, g in enumerate(text.strip().splitlines())]


def test_owner_glob_semantics():
    r = _rules("""
* @all
*.md @docs
/docs/ @writers
app/models/ @db
/app/routes/*.py @api
/web/**/gen/ @robots
/pkg/** @pkgs
/exact.txt
""")
    m = lambda p: eo.match(p, r)  # noqa: E731
    assert m("setup.py") == ["@all"]
    assert m("README.md") == ["@docs"]
    assert m("sub/dir/notes.md") == ["@docs"]
    assert m("docs/guide.md") == ["@writers"]          # later rule beats *.md
    assert m("app/models/user.py") == ["@db"]
    assert m("x/app/models/user.py") == ["@all"]        # contains a slash -> anchored at the root
    assert m("app/routes/a.py") == ["@api"]
    assert m("app/routes/sub/a.py") == ["@all"]         # * does not cross /
    assert m("web/a/b/gen/out.ts") == ["@robots"]
    assert m("web/gen/out.ts") == ["@robots"]           # ** matches zero directories
    assert m("pkg/a/b.py") == ["@pkgs"]
    assert m("exact.txt") == []                         # a rule with no handles un-owns
    assert m("./app/models/user.py") == ["@db"]


def test_owners_missing_and_locations(tmp_path):
    assert eo.parse(tmp_path) == []
    assert eo.match("a.py", []) == []
    _write(tmp_path, {".github/CODEOWNERS": "# c\n\n* @gh  # inline\n", "CODEOWNERS": "* @root\n"})
    rules = eo.parse(tmp_path)
    assert rules == [{"path_glob": "*", "handles": ["@gh"], "ord": 0}]  # .github wins
    assert eo.extract(tmp_path) == {"symbols": [], "edges": []} and eo.link({"symbols": [], "edges": []}) == []
