"""Why a new test fails before the code exists: one reason per failing test, read from the runner's text output.

The autonomy rule for /factory:tests is that a test is frozen only when it fails for the intended reason: the behaviour is
missing (a skeleton function raises NotImplementedError, an assertion or a status code does not hold yet). A test
that fails because it is broken itself (it cannot be imported, it reads an attribute that is not there, it names
something undefined, it does not parse, or the runner cannot collect it) would never pass, and freezing it hands the
implement step a test it cannot satisfy (review finding: a test did `import app.auth`, then `from app.main import
app`, which rebinds `app`; `monkeypatch.setattr(app.auth, ...)` raised AttributeError, the run "failed first" with an
ImportError because the module under test did not exist yet, and the wrong reason was never seen).

Reasons (REASONS): not_implemented, assertion, status_mismatch and other are acceptable; import_error,
attribute_error, name_error, syntax_error and collection_error (WRONG) mean the test must be fixed before it is frozen.
The ledger's `failing_first_reasons` guard uses the same two lists, so an import error of the module under test
counts as wrong here too: the tests step creates a skeleton of that module first, so its import succeeds.

Runners: pytest (the summary and failure sections of `-rA` output) and vitest (the default reporter: `FAIL  file >
suite > test` blocks and the per-file `(N tests | M failed)` lines). Standard library only; imported by
scripts/tests_failing_first.py.
"""

from __future__ import annotations

import posixpath
import re

REASONS = ("not_implemented", "assertion", "status_mismatch", "import_error", "attribute_error", "name_error",
           "syntax_error", "collection_error", "other")
INTENDED = ("not_implemented", "assertion", "status_mismatch")
WRONG = frozenset({"import_error", "attribute_error", "name_error", "syntax_error", "collection_error"})

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# ---------------------------------------------------------------------------------------------------------- pytest

#: `_____ test_name _____`, `_____ ERROR collecting tests/x.py _____`, `_____ ERROR at setup of test_y _____`
PY_SECTION = re.compile(r"^_{3,} (?P<title>.+?) _{3,}$")
PY_BANNER = re.compile(r"^={3,}.*={3,}$|^!{3,}.*!{3,}$")
#: `FAILED tests/x.py::test_a - AttributeError: ...`, `ERROR tests/x.py`, `PASSED tests/x.py::test_b`
PY_SUMMARY = re.compile(r"^(?P<kind>PASSED|FAILED|ERROR|XPASS|XFAIL)\s+(?P<id>[^\s]+?)(?:\s+-\s+(?P<msg>.*))?$")
#: the exception line pytest prints after `E`: `E   ModuleNotFoundError: No module named 'app.x'`, `E   NotImplementedError`
PY_EXC = re.compile(r"^(?P<type>(?:[A-Za-z_]\w*\.)*(?:[A-Z]\w*(?:Error|Exception|Failed|Exit|Interrupt)|Failed))(?::\s?(?P<msg>.*))?$")
#: a rewritten assert: `E       assert 200 == 429`
PY_ASSERT = re.compile(r"^assert\b")
#: an HTTP status compared: `assert 200 == 429`, `+  where 200 = <Response [200 OK]>.status_code`
PY_STATUS = re.compile(r"\bassert\s+[1-5]\d\d\s*(?:==|!=)\s*[1-5]\d\d\b|\.status_code\b|<Response \[[1-5]\d\d\b")
PY_FIXTURE = re.compile(r"^fixture '[^']+' not found")
PY_NO_MODULE = re.compile(r"No module named '(?P<mod>[\w.]+)'")
PY_CANNOT_IMPORT = re.compile(r"cannot import name '(?P<name>\w+)' from '(?P<mod>[\w.]+)'")
PY_NOT_FOUND = re.compile(r"^ERROR: (?:file or directory not found|not found): (?P<path>\S+)")

PY_TYPES = {
    "NotImplementedError": "not_implemented",
    "AssertionError": "assertion",
    "Failed": "assertion",  # pytest.fail / pytest.raises: DID NOT RAISE
    "ImportError": "import_error",
    "ModuleNotFoundError": "import_error",
    "AttributeError": "attribute_error",
    "NameError": "name_error",
    "UnboundLocalError": "name_error",
    "SyntaxError": "syntax_error",
    "IndentationError": "syntax_error",
    "TabError": "syntax_error",
}


def classify_python(exc_type: str, message: str = "", context: str = "") -> str:
    """Reason for a Python exception type (dotted names allowed) and message; `context` is the rest of the E lines."""
    base = exc_type.rsplit(".", 1)[-1]
    reason = PY_TYPES.get(base, "other")
    if reason == "assertion" and PY_STATUS.search(message + "\n" + context):
        return "status_mismatch"
    return reason


def _py_reason(e_lines: list[str]) -> tuple[str, str]:
    """(reason, detail) from the `E` lines of one pytest failure section."""
    exc = None
    for line in e_lines:
        m = PY_EXC.match(line.strip())
        if m:
            exc = m
    context = "\n".join(e_lines)
    if exc is not None:
        detail = exc.group(0)
        return classify_python(exc.group("type"), exc.group("msg") or "", context), detail
    for line in e_lines:
        s = line.strip()
        if PY_FIXTURE.match(s):
            return "name_error", s
        if PY_ASSERT.match(s):
            return ("status_mismatch" if PY_STATUS.search(context) else "assertion"), s
    return "other", (e_lines[-1].strip() if e_lines else "")


def _module_of(detail: str) -> str | None:
    m = PY_NO_MODULE.search(detail) or PY_CANNOT_IMPORT.search(detail)
    return m.group("mod") if m else None


def parse_pytest(output: str) -> dict:
    """{"results": [{test, file, reason, detail, module?}], "passed": {file: n}} from pytest output run with -rA."""
    text = ANSI.sub("", output)
    sections: list[dict] = []
    cur = None
    not_found: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        m = PY_SECTION.match(line)
        if m:
            cur = {"title": m.group("title"), "e": []}
            sections.append(cur)
            continue
        if PY_BANNER.match(line):
            cur = None
            continue
        nf = PY_NOT_FOUND.match(line)
        if nf:
            not_found.append(nf.group("path"))
        if cur is not None and (line.startswith("E ") or line == "E"):
            cur["e"].append(line[1:].strip())

    collect = {s["title"][len("ERROR collecting "):].strip(): s for s in sections if s["title"].startswith("ERROR collecting ")}
    setup = [s for s in sections if s["title"].startswith(("ERROR at setup of ", "ERROR at teardown of "))]
    failures = [s for s in sections if not s["title"].startswith("ERROR ") and s["title"] not in ("warnings summary",)]

    def take(pool: list[dict], name: str) -> dict | None:
        for s in pool:
            title = s["title"].split(" of ", 1)[1] if s["title"].startswith("ERROR at ") else s["title"]
            if not s.get("used") and (title == name or title.endswith("." + name)):
                s["used"] = True
                return s
        return None

    results: list[dict] = []
    passed: dict[str, int] = {}
    for raw in text.splitlines():
        m = PY_SUMMARY.match(raw.rstrip())
        if not m:
            continue
        kind, tid, msg = m.group("kind"), m.group("id"), (m.group("msg") or "")
        file = tid.split("::", 1)[0]
        if kind in ("PASSED", "XPASS", "XFAIL"):
            passed[file] = passed.get(file, 0) + 1
            continue
        if kind == "ERROR" and "::" not in tid:
            sec = collect.get(tid)
            reason, detail = _py_reason(sec["e"]) if sec else ("collection_error", msg)
            if reason not in WRONG:
                reason = "collection_error"  # a module-level exception of any other type still stops collection
        else:
            name = tid.split("::")[-1]
            sec = take(setup if kind == "ERROR" else failures, name)
            if sec is not None:
                reason, detail = _py_reason(sec["e"])
            else:  # no section (a -q -rA run with --tb=no): the summary line names the exception
                em = PY_EXC.match(msg.strip())
                reason = classify_python(em.group("type"), em.group("msg") or "") if em else (
                    "assertion" if msg.startswith("assert") else "other")
                detail = msg
        row = {"test": tid, "file": file, "reason": reason, "detail": detail}
        mod = _module_of(detail) if reason == "import_error" else None
        if mod:
            row["module"] = mod
        results.append(row)
    for path in not_found:
        results.append({"test": path, "file": path, "reason": "collection_error", "detail": "file or directory not found"})
    return {"results": results, "passed": passed}


# ---------------------------------------------------------------------------------------------------------- vitest

#: ` FAIL  tests/a.test.ts > limiter > blocks after 100` and the suite-level ` FAIL  tests/a.test.ts [ tests/a.test.ts ]`
VT_FAIL = re.compile(r"^\s*FAIL\s+(?P<id>\S+(?: > .+?)?)(?:\s+\[ (?P<suite>[^\]]+) \])?\s*$")
#: ` ❯ tests/a.test.ts (8 tests | 7 failed) 8ms`, ` ✓ tests/b.test.ts (3 tests) 2ms`
VT_FILE = re.compile(r"^\s*[❯✓×↓]\s+(?P<file>\S+)\s+\((?P<n>\d+) tests?(?: \| (?P<rest>[^)]*))?\)")
VT_DIVIDER = re.compile(r"^⎯{3,}")
#: the error line under a FAIL header: `AssertionError: expected 200 to be 429 // Object.is equality`, `Error: not implemented`
VT_ERR = re.compile(r"^(?P<type>[A-Z]\w*(?:Error|Exception)|Error)(?:\s*\[[\w-]+\])?:\s?(?P<msg>.*)$")
VT_NOT_IMPLEMENTED = re.compile(r"not implemented", re.I)
VT_STATUS = re.compile(r"expected (?:\S+ )?(?:status )?[1-5]\d\d to (?:be|equal|deeply equal|strictly equal|have status) [1-5]\d\d\b"
                       r"|to have (?:http )?status\b")
VT_IMPORT = re.compile(r"Cannot find (?:module|package) '(?P<mod>[^']+)'|Failed to (?:resolve|load) (?:import|url) \"?(?P<mod2>[^\"\s]+)\"?"
                       r"|does not provide an export named '(?P<name>[^']+)'|ERR_MODULE_NOT_FOUND")
VT_SYNTAX = re.compile(r"Transform failed|PARSE_ERROR|Unexpected token|Expected .* but found|^SyntaxError")
VT_ATTR = re.compile(r"Cannot (?:read|set) propert(?:y|ies) of (?:undefined|null)|is not a (?:function|constructor)\b|is not iterable")
VT_COLLECT = re.compile(r"No test (?:suite|files?) found")


def classify_node(err_type: str, message: str, block: str = "", suite_level: bool = False) -> str:
    """Reason for a vitest error line (`Type: message`) and the rest of its block."""
    whole = f"{err_type}: {message}\n{block}"
    if VT_NOT_IMPLEMENTED.search(message) or err_type == "NotImplementedError":
        return "not_implemented"
    if VT_IMPORT.search(whole):
        return "import_error"
    if err_type == "AssertionError":
        return "status_mismatch" if VT_STATUS.search(message) else "assertion"
    if err_type == "ReferenceError":
        return "name_error"
    if err_type == "SyntaxError" or VT_SYNTAX.search(message):
        return "syntax_error"
    if err_type == "TypeError" and VT_ATTR.search(message):
        return "attribute_error"
    if VT_COLLECT.search(message):
        return "collection_error"
    return "collection_error" if suite_level else "other"


def parse_vitest(output: str) -> dict:
    """{"results": [{test, file, reason, detail, module?}], "passed": {file: n}} from vitest's default reporter."""
    lines = [ANSI.sub("", ln).rstrip() for ln in output.splitlines()]
    results: list[dict] = []
    passed: dict[str, int] = {}
    seen: set[str] = set()
    for line in lines:
        m = VT_FILE.match(line)
        if m and m.group("file") not in seen:
            seen.add(m.group("file"))
            total = int(m.group("n"))
            rest = m.group("rest") or ""
            failed = sum(int(x) for x in re.findall(r"(\d+) failed", rest))
            skipped = sum(int(x) for x in re.findall(r"(\d+) (?:skipped|todo)", rest))
            if total - failed - skipped > 0:
                passed[m.group("file")] = total - failed - skipped
    i = 0
    while i < len(lines):
        m = VT_FAIL.match(lines[i])
        if not m:
            i += 1
            continue
        tid = m.group("id").strip()
        suite_level = m.group("suite") is not None
        file = tid.split(" > ", 1)[0]
        j = i + 1
        block: list[str] = []
        while j < len(lines) and not VT_DIVIDER.match(lines[j]) and not VT_FAIL.match(lines[j]):
            block.append(lines[j])
            j += 1
        err = next((VT_ERR.match(b.strip()) for b in block if VT_ERR.match(b.strip())), None)
        if err is not None:
            etype, msg = err.group("type"), err.group("msg")
            detail = err.group(0)
        else:
            etype, msg = "Error", (block[0].strip() if block else "")
            detail = msg
        rest = "\n".join(block)
        reason = classify_node(etype, msg, rest, suite_level)
        if suite_level and reason not in WRONG:
            reason = "collection_error"  # a file that fails before any test runs cannot be frozen as failing first
        row = {"test": tid if not suite_level else file, "file": file, "reason": reason, "detail": detail}
        if reason == "import_error":
            im = VT_IMPORT.search(f"{detail}\n{rest}")
            mod = im and (im.group("mod") or im.group("mod2"))
            if mod:
                row["module"] = mod
        results.append(row)
        i = j
    return {"results": results, "passed": passed}


# ---------------------------------------------------------------------------------------------------------- shared

def detect_runner(command: str) -> str | None:
    """`pytest` or `vitest` for a test command, None when neither shape is known."""
    if re.search(r"\bpytest\b|\bpy\.test\b", command):
        return "pytest"
    if re.search(r"\bvitest\b|\b(?:pnpm|npm|yarn|bun)\b(?:\s+run)?\s+test\b", command):
        return "vitest"
    return None


def parse(output: str, runner: str) -> dict:
    if runner == "pytest":
        return parse_pytest(output)
    if runner == "vitest":
        return parse_vitest(output)
    raise ValueError(f"unknown runner {runner!r}; supported: pytest, vitest")


def is_wrong(reason: str) -> bool:
    return reason in WRONG


def module_candidates(module: str, test_file: str, runner: str) -> list[str]:
    """Repo-relative files an import names: `app.x` -> app/x.py, app/x/__init__.py; `../src/x` (from tests/a.test.ts)
    -> src/x.ts, src/x/index.ts, ...; `@/lib/x` -> src/lib/x.*. A bare package name has none (it is not repo code)."""
    if runner == "pytest":
        base = module.replace(".", "/")
        return [base + ".py", base + "/__init__.py"]
    if module.startswith("@/"):
        base = "src/" + module[2:]
    elif module.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(test_file), module))
    elif module.startswith("/"):
        return []
    else:
        return []
    exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
    stem, ext = posixpath.splitext(base)
    out = [base] if ext in exts else []
    out += [base + e for e in exts] + [base + "/index" + e for e in exts]
    if ext == ".js":  # TypeScript's ESM spelling: import "./x.js" resolves to x.ts
        out += [stem + e for e in (".ts", ".tsx")]
    return out


def advice(row: dict, under_test: bool | None) -> str:
    """One line saying what to fix for a wrong reason."""
    r = row["reason"]
    mod = row.get("module")
    if r == "import_error" and mod and under_test:
        return (f"the module under test {mod} cannot be imported: create its skeleton inside the allowed paths (each "
                f"function the tests call raising NotImplementedError, nothing else) and rerun")
    if r == "import_error":
        return f"the test imports {mod or 'something'} that is not the module under test and does not resolve: fix the import"
    if r == "attribute_error":
        return ("the test reads an attribute that is not there (a name rebound by a later import, such as `import app.auth` "
                "then `from app.main import app`, or a function the skeleton lacks): fix the test or add the function to the skeleton")
    if r == "name_error":
        return "the test names something undefined (a missing import or fixture): fix the test"
    if r == "syntax_error":
        return "the test does not parse: fix the test"
    if r == "collection_error":
        return "the runner could not collect the file (an error at import time, or no test in it): fix the test"
    return ""
