"""Regression tests for the adversarial review of the python checkers (stubs, dead_code, api_contract,
module_state, route_auth): idioms that were false positives, files that crashed a checker so it read as
clean, and the exit-2 contract for misuse. Works against the plugin and the kit through the `repo` fixture."""

from __future__ import annotations


def _run_checker(repo, name: str):
    """(rc, findings) where findings = [(key, 'file:line: message')]."""
    if repo.form == "plugin":
        r = repo.checker(name)
    else:
        r = repo.run_script(_KIT[name], "--root", str(repo.path))
    return r, [tuple(line.split("\t", 1)) for line in r.out.splitlines() if "\t" in line]


_KIT = {"stubs": "check_stubs.py", "dead_code": "check_dead_code.py", "api_contract": "check_api_contract.py",
        "route_auth": "check_route_auth.py", "module_state": "check_module_state.py"}

def _keys(rows):
    return {k for k, _ in rows}


def _by_line(rows, code):
    return sorted(int(m.split(":")[1]) for k, m in rows if k.endswith("|" + code))


def test_stubs_checker_skips_generic_protocols_dunders_abc_and_singledispatch(repo):
    repo.write(
        "app/idioms.py",
        """\
import functools
import typing as t
from abc import ABC, ABCMeta
from typing import Protocol, TypeVar

T = TypeVar("T")


class Repo(Protocol[T]):
    def get(self, key): ...


class Repo2(t.Protocol[T]):
    def get(self, key): ...


class Base(metaclass=ABCMeta):
    def hook(self):
        pass


class Base2(ABC):
    def hook(self):
        pass


class Ctx:
    def __init__(self):
        pass

    def __exit__(self, *exc):
        ...


@functools.singledispatch
def render(x):
    raise NotImplementedError


def real_stub():
    pass
""",
    )
    r, rows = _run_checker(repo, "stubs")
    stubs = [m for k, m in rows if k.endswith("|STUB-BODY")]
    assert len(stubs) == 1 and "real_stub" in stubs[0], r.text


def test_stubs_checker_class_mutable_covers_registries_and_skips_pydantic_subclasses(repo):
    repo.write(
        "app/models.py",
        """\
from collections import defaultdict, deque, Counter, OrderedDict
from pydantic import BaseModel


class Base(BaseModel):
    pass


class Item(Base):
    tags: list[str] = []
    model_config = {"from_attributes": True}

    class Config:
        schema_extra = {"a": 1}


class Svc:
    seen = defaultdict(list)
    queue = deque()
    hits = Counter()
    order = OrderedDict()
    cache = {}

    class Meta:
        ordering = ["id"]
""",
    )
    r, rows = _run_checker(repo, "stubs")
    msgs = " ".join(m for k, m in rows if k.endswith("|CLASS-MUTABLE"))
    for name in ("Svc.seen", "Svc.queue", "Svc.hits", "Svc.order", "Svc.cache"):
        assert f"'{name}'" in msgs, r.text
    assert "Item" not in msgs and "Config" not in msgs and "Meta" not in msgs, r.text


def test_stubs_checker_cred_param_matches_whole_segments_only(repo):
    repo.write(
        "app/creds.py",
        """\
from fastapi import APIRouter

router = APIRouter()


class PasswordReset:
    pass


@router.post("/a/{token_id}")
def a(token_id: int, max_tokens: int, secret_name: str, password_reset: PasswordReset):
    return 1


@router.get("/b/{api_key}")
def b(api_key: str):
    return 1


@router.get("/c")
def c(pwd: str, authorization: str, client_secret_key: str):
    return 1
""",
    )
    r, rows = _run_checker(repo, "stubs")
    msgs = " ".join(m for k, m in rows if k.endswith("|CRED-PARAM"))
    for good in ("'pwd'", "'authorization'", "'client_secret_key'", "'api_key'"):
        assert good in msgs, r.text
    for bad in ("token_id", "max_tokens", "secret_name", "password_reset"):
        assert bad not in msgs, (bad, r.text)


def test_stubs_checker_todo_ticket_ids(repo):
    repo.write(
        "app/todo.py",
        "# TODO: switch to SHA-256\n# todo: lowercase counts\n# TODO(ABC-12): fine\n# XXX-XXX-XXXX phone format\n# TODO #45 fine\nx = 1\n",
    )
    r, rows = _run_checker(repo, "stubs")
    assert _by_line(rows, "TODO-NO-ID") == [1, 2], r.text


def test_checkers_suppression_survives_form_feed_and_line_separator(repo):
    repo.write("app/ff.py", "x = 1\n\x0c\ndef f(): pass  # stubs: ok reason\n")
    repo.write("app/ls.py", 's = "a\u2028b"\ndef g(): pass  # stubs: ok reason\n')
    repo.write("app/st.py", "x = 1\n\x0c\nCACHE = {}  # state: ok reason\n")
    r, rows = _run_checker(repo, "stubs")
    assert not any(k.startswith("stubs|app/ff.py") or k.startswith("stubs|app/ls.py") for k, _ in rows), r.text
    r, rows = _run_checker(repo, "module_state")
    assert not any("app/st.py" in k for k, _ in rows), r.text


def test_checkers_read_bom_and_coding_cookie_files_and_never_crash_on_them(repo):
    (repo.path / "app").mkdir(exist_ok=True)
    (repo.path / "app" / "bom.py").write_bytes(b"\xef\xbb\xbfX = 1\n")
    (repo.path / "app" / "latin.py").write_bytes(b"# -*- coding: latin-1 -*-\nS = '\xe9'\n")
    repo.write("app/bad.py", "CACHE = {}\n")
    for name, prefix in (("stubs", "stubs|"), ("dead_code", "dead|"), ("module_state", "state|"), ("route_auth", "route-auth|syntax")):
        r, rows = _run_checker(repo, name)
        assert r.rc in (0, 1), (name, r.text)
        assert not any(("bom.py" in k or "latin.py" in k) and k.lower().endswith("syntax") for k, _ in rows), (name, r.text)
        assert not any(k.startswith("route-auth|syntax") for k, _ in rows), (name, r.text)
    r, rows = _run_checker(repo, "module_state")
    assert "state|app/bad.py|CACHE" in _keys(rows), r.text


def test_checkers_exit_2_on_misuse(repo):
    for name in ("stubs", "dead_code", "api_contract", "module_state", "route_auth"):
        for extra in (["--root", str(repo.path / "does-not-exist")], ["--bogus"]):
            r = repo.checker(name, *extra) if repo.form == "plugin" else repo.run_script(_KIT[name], *extra)
            assert r.rc == 2, (name, extra, r.text)


def test_route_auth_accepts_annotated_aliases_security_and_include_router_dependencies(repo):
    repo.write(
        "app/deps.py",
        "from typing import Annotated\nfrom fastapi import Depends\nfrom app.auth import api_key_auth\n\nCurrentUser = Annotated[str, Depends(api_key_auth)]\n",
    )
    repo.write(
        "app/r1.py",
        """\
from typing import Annotated
from fastapi import APIRouter, Depends, Security

from app.auth import api_key_auth
from app.deps import CurrentUser

router = APIRouter()


@router.get("/a")
def a(u: Annotated[str, Depends(api_key_auth)]):
    return 1


@router.get("/b")
def b(u: CurrentUser):
    return 1


@router.get("/c", dependencies=[Security(api_key_auth)])
def c():
    return 1


@router.get("/open")
def open_route():
    return 1
""",
    )
    repo.write(
        "app/r2.py",
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n@router.get(\"/d\")\ndef d():\n    return 1\n",
    )
    main = repo.read("app/main.py")
    repo.write("app/main.py", main + "\nfrom app import r1, r2\nfrom app.auth import api_key_auth as _auth\n\napp.include_router(r1.router)\napp.include_router(r2.router, dependencies=[Depends(api_key_auth)])\n")
    if repo.form == "plugin":
        repo.set_config(auth={"dependency_names": ["api_key_auth"]})
    r, rows = _run_checker(repo, "route_auth")
    assert _keys(rows) == {"route-auth|GET /open"}, r.text


def test_api_contract_resolves_constant_prefixes_and_knows_fastapi_builtins(repo):
    repo.write(
        "app/main.py",
        """\
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings


class Cfg:
    API_V1_STR = "/api/v1"


app = FastAPI()
api = APIRouter()
other = APIRouter()
app.mount("/static", StaticFiles(directory="static"), name="static")


@api.get("/items")
def items():
    return []


@api.post("/pay")
def pay():
    return 1


@other.get("/things")
def things():
    return []


app.include_router(api, prefix=Cfg.API_V1_STR)
app.include_router(other, prefix=settings.UNKNOWN_PREFIX)
""",
    )
    repo.write(
        "frontend/src/a.ts",
        """\
const qs = "";
fetch("/api/v1/items");
fetch(`/api/v1/items${qs}`);
fetch("/static/logo.png");
fetch("/openapi.json");
fetch("/v2/things");
fetch("/api/v1/pay", {headers: {a: 1}, body: JSON.stringify({method: "card"})});
fetch("/api/v1/pay", {method: "POST"});
const logoUrl = "/logo.svg";
const loginUrl = "/login";
const missingUrl = "/api/v1/missing";
""",
    )
    repo.write("frontend/pages/api/hello.ts", "export default function handler() {}\n")
    repo.write("frontend/src/b.ts", 'fetch("/api/hello");\n')
    repo.write("README.md", "GET /docs\nGET /redoc\n")
    r, rows = _run_checker(repo, "api_contract")
    assert _keys(rows) == {"contract|GET /api/v1/pay", "contract|GET /api/v1/missing"}, r.text


def test_api_contract_scan_is_linear_on_an_unterminated_string(repo):
    repo.write("frontend/public/evil.js", 'fetch("' + "\\" * 60 + "\n")
    repo.write("frontend/src/evil.ts", 'fetch("' + "\\" * 60 + "\n")
    r, rows = _run_checker(repo, "api_contract")
    assert r.rc in (0, 1), r.text
