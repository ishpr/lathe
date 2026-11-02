#!/usr/bin/env python3
"""Python: stubs, untracked TODOs and unsafe patterns that prose rules could not enforce.

Why this exists (REC-34): CLAUDE.md asks for these things and nothing
checked them. Codes, each one finding line per occurrence:
  TODO-NO-ID          TODO / FIXME comment (any case) with no ticket id (config ticket_id_pattern; SHA-256,
                      UTF-8, ISO-8601 and the like are not ticket ids)
  STUB-BODY           function body is only pass / ... / raise NotImplementedError / `return None` with a
                      TODO; abstract methods, overloads, singledispatch, dunder methods, Protocol (also
                      Protocol[T]) and ABC classes, and tests are skipped
  ENV-OUTSIDE-CONFIG  os.environ / os.getenv / os.environb outside the allowed config module(s)
  CRED-PARAM          route function parameter whose name has a whole segment password/passwd/pwd/secret/
                      token/api_key/authorization/credentials (not max_tokens, token_id, secret_name), that is not read from
                      Header/Cookie/Form/Body/Depends/Security, is not a body model, so it
                      travels in the URL query string
  CLASS-MUTABLE       class-level list/dict/set/defaultdict/deque/OrderedDict/Counter shared by every
                      instance (ClassVar, Enum, pydantic models incl. project subclasses, nested Config/Meta, model_config
                      and UPPER_CASE non-empty tables are skipped)
A line carrying `# stubs: ok <reason>` is skipped.

Output: "stubs|<file>|<CODE><TAB>file:line: message". Exit 0 none, 1 findings, 2 misuse/crash.
Usage: stubs.py --root DIR [--config PATH]
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import sys

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib"))
import sdlc as cl  # noqa: E402


class Env:
    """Where the checker looks: project root, app files, config, test/glob predicates."""

    def __init__(self, argv):
        opts = _opts(argv)
        # an explicit --root wins over CLAUDE_PROJECT_DIR (which project_root prefers)
        self.root = os.path.abspath(opts["--root"]) if opts.get("--root") else cl.project_root(None)
        if not os.path.isdir(self.root):
            raise ValueError(f"--root {self.root} is not a directory")
        cfg = cl.load_config(self.root)
        extra = opts.get("--config")
        if extra and os.path.isfile(extra):
            try:
                with open(extra, encoding="utf-8") as fh:
                    cfg = cl.deep_merge(cfg, json.load(fh))
            except (OSError, ValueError):
                pass
        self.cfg = cfg
        self.files = [
            rel
            for rel in cl.walk_files(self.root, cfg["paths"].get("app", []), [".py"])
            if not cl.is_test_path(cfg, rel) and _scannable(self.root, rel)
        ]
        self.opts = opts
        if not self.files:
            sys.stderr.write(f"{os.path.basename(sys.argv[0])}: no files to scan under {self.root} (check paths.app in .claude/sdlc.json); a clean result here means nothing\n")

    def setting(self, key, default):
        value = self.cfg.get(key)
        return default if value in (None, "") else value

    def match(self, rel, patterns):
        return cl.matches(rel, patterns)

    def read(self, rel):
        # tokenize.open honours a BOM and a PEP 263 coding line, like the interpreter does
        with tokenize.open(os.path.join(self.root, rel)) as fh:
            return fh.read()


def _scannable(root, rel):
    """A regular file, or a symlink (a broken one is reported rather than skipped); never a FIFO or device."""
    full = os.path.join(root, rel)
    return os.path.islink(full) or os.path.isfile(full)


PARSE_ERRORS = (SyntaxError, ValueError, OSError, RecursionError, MemoryError)  # UnicodeDecodeError is a ValueError
KNOWN_OPTS = ("--root", "--config", "--strip-prefix")


def _opts(argv):
    out = {}
    args = list(argv)
    i = 0
    while i < len(args):
        if args[i] in KNOWN_OPTS:
            if i + 1 >= len(args):
                raise ValueError(f"{args[i]} needs a value")
            out[args[i]] = args[i + 1]
            i += 2
        elif args[i].startswith("-"):
            raise ValueError(f"unknown option {args[i]} (options: {', '.join(KNOWN_OPTS)}, --help)")
        else:
            i += 1
    return out


METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}
CRED_SEGMENTS = {"password", "passwd", "pwd", "secret", "token", "apikey", "authorization", "credentials", "credential"}
CRED_BENIGN_LAST = {"id", "ids", "name", "names", "count", "max", "min", "limit", "ttl", "type", "kind", "field", "header", "url", "path", "file", "length", "len", "expires", "seconds"}
CRED_BENIGN_FIRST = {"max", "min", "num", "count", "n"}
SCALAR_ANN = {"str", "int", "float", "bool", "bytes", "SecretStr", "UUID", "Decimal", "Any", "None"}
ANN_WRAPPERS = {"Optional", "Union", "Annotated", "Literal", "List", "Dict", "Set", "Tuple", "list", "dict", "set", "tuple", "Sequence", "Iterable"}
NOT_TICKET = {"SHA", "UTF", "ISO", "RFC", "HTTP", "HTTPS", "TLS", "SSL", "MD", "AES", "RSA", "HS", "RS", "ES", "TCP", "UDP", "IPV", "PEP", "BCP", "CVE"}
STUB_SKIP_DECOS = {"abstractmethod", "overload", "singledispatch", "singledispatchmethod", "register", "abstractproperty", "abstractclassmethod", "abstractstaticmethod"}
SAFE_SOURCES = {"Depends", "Security", "Header", "Cookie", "Form", "Body"}
ENV_ATTRS = {"environ", "getenv", "environb"}
MUTABLE_CTORS = {"list", "dict", "set", "defaultdict", "deque", "OrderedDict", "Counter"}
SAFE_CLASS_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "BaseModel", "BaseSettings", "TypedDict", "NamedTuple", "Protocol"}
SUPPRESS = "stubs: ok"
TODO_RE = re.compile(r"\b(?:TODO|FIXME)\b|\b(?i:todo|fixme)(?=\s*[:(])")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _base_name(node):
    """Class base name with subscripts unwrapped: Protocol[T], t.Protocol[T], Generic[T]."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return _name(node)


def _has_ticket(ticket_re, text):
    return any(m.group(0).split("-", 1)[0] not in NOT_TICKET for m in ticket_re.finditer(text))


def _deco_names(fn):
    out = set()
    for d in fn.decorator_list:
        out.add(_name(d.func if isinstance(d, ast.Call) else d))
    return out


def _comments(src):
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, SyntaxError, ValueError):
        pass
    return out


def _stub_kind(fn, comments):
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1:
        return None
    s = body[0]
    if isinstance(s, ast.Pass):
        return "pass"
    if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis:
        return "..."
    if isinstance(s, ast.Raise) and s.exc is not None:
        exc = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
        if _name(exc) == "NotImplementedError":
            return "raise NotImplementedError"
    if isinstance(s, ast.Return) and (s.value is None or (isinstance(s.value, ast.Constant) and s.value.value is None)):
        end = getattr(fn, "end_lineno", fn.lineno)
        if any(fn.lineno <= ln <= end and TODO_RE.search(text) for ln, text in comments):
            return "return None with a TODO"
    return None


def _safe_default(arg, default):
    if isinstance(default, ast.Call) and _name(default.func) in SAFE_SOURCES:
        return True
    ann = getattr(arg, "annotation", None)
    if ann is not None:
        for sub in ast.walk(ann):
            if isinstance(sub, ast.Call) and _name(sub.func) in SAFE_SOURCES:
                return True
    return False


def _cred_name(name):
    """Whole-segment match: password, client_secret_key, x_api_key; not max_tokens, token_id, secret_name."""
    segs = [s for s in name.lower().split("_") if s]
    if len(segs) > 1 and (segs[-1] in CRED_BENIGN_LAST or segs[0] in CRED_BENIGN_FIRST):
        return False
    return any(s in CRED_SEGMENTS for s in segs) or any(a == "api" and b == "key" for a, b in zip(segs, segs[1:]))


def _body_model(ann):
    """True when the annotation names a non-scalar type (a pydantic body model), which FastAPI reads from the body."""
    if isinstance(ann, ast.Name):
        return ann.id not in SCALAR_ANN and ann.id not in ANN_WRAPPERS
    if isinstance(ann, ast.Attribute):
        return ann.attr not in SCALAR_ANN and ann.attr not in ANN_WRAPPERS
    if isinstance(ann, ast.Subscript):
        return _body_model(ann.value) or _body_model(ann.slice)
    if isinstance(ann, ast.BinOp):
        return _body_model(ann.left) or _body_model(ann.right)
    if isinstance(ann, (ast.Tuple, ast.List)):
        return any(_body_model(e) for e in ann.elts)
    return False


def _cred_params(fn):
    a = fn.args
    pos = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    pairs = list(zip(pos, defaults)) + list(zip(a.kwonlyargs, a.kw_defaults))
    out = []
    for arg, default in pairs:
        n = arg.arg
        if n in ("self", "cls") or not _cred_name(n) or _safe_default(arg, default) or _body_model(getattr(arg, "annotation", None)):
            continue
        out.append(n)
    return out


def _is_route(fn):
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in METHODS:
            return True
    return False


def _mutable_value(v):
    if isinstance(v, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return isinstance(v, ast.Call) and _name(v.func) in MUTABLE_CTORS


def _is_constant_table(name, value):
    bare = name.lstrip("_")
    if name.startswith("__") and name.endswith("__"):
        return True
    empty = (isinstance(value, ast.Dict) and not value.keys) or (isinstance(value, (ast.List, ast.Set)) and not value.elts)
    return bare.isupper() and not isinstance(value, ast.Call) and not empty


def _is_classvar(ann):
    return ann is not None and any(isinstance(n, (ast.Name, ast.Attribute)) and _name(n) == "ClassVar" for n in ast.walk(ann))


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel, lines, comments, allow_env, safe, out):
        self.rel, self.lines, self.comments, self.allow_env, self.safe, self.out = rel, lines, comments, allow_env, safe, out
        self.classes = []

    def add(self, code, line, msg, end=None):
        text = "\n".join(self.lines[line - 1: end or line])
        if SUPPRESS in text:
            return
        self.out.append((f"stubs|{self.rel}|{code}", f"{self.rel}:{line}: {msg}"))

    def visit_ClassDef(self, node):
        bases = {n for n in (_base_name(b) for b in node.bases) if n}
        bases |= {n for n in (_base_name(kw.value) for kw in node.keywords if kw.arg == "metaclass") if n}
        nested_config = bool(self.classes) and node.name in ("Config", "Meta")  # pydantic v1 Config, Django Meta
        if not (bases & self.safe) and not nested_config:
            for st in node.body:
                if isinstance(st, ast.Assign):
                    targets, value, ann = [t for t in st.targets if isinstance(t, ast.Name)], st.value, None
                elif isinstance(st, ast.AnnAssign) and st.value is not None and isinstance(st.target, ast.Name):
                    targets, value, ann = [st.target], st.value, st.annotation
                else:
                    continue
                if not _mutable_value(value) or _is_classvar(ann):
                    continue
                for t in targets:
                    if t.id != "model_config" and not _is_constant_table(t.id, value):
                        self.add("CLASS-MUTABLE", st.lineno, f"class attribute '{node.name}.{t.id}' is a mutable object shared by every instance and every request; build it in __init__ (or use dataclasses.field(default_factory=...)), or mark it ClassVar if sharing is intended")
        self.classes.append(bases)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node):
        in_protocol = any("Protocol" in b for b in self.classes)
        in_abc = any({"ABC", "ABCMeta"} & b for b in self.classes)
        dunder = node.name.startswith("__") and node.name.endswith("__")  # __init__/__exit__ with no work are idiomatic
        if not in_protocol and not in_abc and not dunder and not (STUB_SKIP_DECOS & _deco_names(node)):
            kind = _stub_kind(node, self.comments)
            if kind:
                self.add("STUB-BODY", node.lineno, f"'{node.name}' has a stub body ({kind}); implement it or delete it, a stub that returns nothing looks like working code", getattr(node, "end_lineno", None))
        if _is_route(node):
            for p in _cred_params(node):
                self.add("CRED-PARAM", node.lineno, f"route '{node.name}' takes credential parameter '{p}' as a plain argument, so it travels in the URL query string and lands in access logs; read it from the Authorization header or a request body model")
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node):
        if not self.allow_env and isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr in ENV_ATTRS:
            self.add("ENV-OUTSIDE-CONFIG", node.lineno, f"os.{node.attr} used outside the config module; read settings once in app/config.py and import the value")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if not self.allow_env and node.module == "os" and any(a.name in ENV_ATTRS for a in node.names):
            self.add("ENV-OUTSIDE-CONFIG", node.lineno, "environment access imported from os outside the config module; read settings once in app/config.py and import the value")


def _safe_class_names(trees):
    """SAFE_CLASS_BASES plus every project class whose base chain reaches one (class Item(Base), Base(BaseModel))."""
    bases = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(n for n in (_base_name(b) for b in node.bases) if n)
    safe = set(SAFE_CLASS_BASES)
    changed = True
    while changed:
        changed = False
        for name, bs in bases.items():
            if name not in safe and bs & safe:
                safe.add(name)
                changed = True
    return safe


def check_file(env, rel, ticket_re, allowed, safe, parsed):
    src, tree = parsed
    lines = src.split("\n")  # not splitlines(): it also splits on \f and U+2028, which ast line numbers do not
    comments = _comments(src)
    out = []
    for ln, text in comments:
        if TODO_RE.search(text) and not _has_ticket(ticket_re, text) and SUPPRESS not in text:
            out.append((f"stubs|{rel}|TODO-NO-ID", f"{rel}:{ln}: {text.strip()[:80]!r} has no ticket id; write the ticket (for example PROJ-123 or #45) next to it, or do the work"))
    _Visitor(rel, lines, comments, env.match(rel, allowed), safe, out).visit(tree)
    return out


def run(env):
    ticket_re = re.compile(env.setting("ticket_id_pattern", r"[A-Z]{2,}-\d+|#\d+"))
    allowed = env.setting("env_access_allowed", ["app/config.py"])
    parsed, bad = {}, {}
    for rel in env.files:
        try:
            src = env.read(rel)
            parsed[rel] = (src, ast.parse(src, filename=rel))
        except PARSE_ERRORS as exc:
            bad[rel] = exc
    safe = _safe_class_names(t for _, t in parsed.values())
    findings = []
    for rel in env.files:
        try:
            if rel in bad:
                raise bad[rel]
            findings.extend(check_file(env, rel, ticket_re, allowed, safe, parsed[rel]))
        except PARSE_ERRORS as exc:
            findings.append((f"stubs|{rel}|SYNTAX", f"{rel}: cannot parse: {type(exc).__name__}: {exc}"))
    return findings


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__)
        return 0
    try:
        env = Env(argv)
        findings = run(env)
    except Exception as exc:  # noqa: BLE001 - a crashing checker must not read as "clean"
        sys.stderr.write(f"stubs: {type(exc).__name__}: {exc}\n")
        return 2
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
