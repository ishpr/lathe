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

Runners: pytest (the summary and failure sections of `-rA` output), vitest (the default reporter: `FAIL  file >
suite > test` blocks and the per-file `(N tests | M failed)` lines) and Playwright (`playwright test`: the `list`
reporter's status lines and numbered failure sections, or the `json` reporter's report, which the recorder asks for
with --reporter=list,json and PLAYWRIGHT_JSON_OUTPUT_FILE). For an end-to-end test the intended reasons are a failed
expectation and an element or page state that never came (an `expect(locator)` or `locator.click` timeout, a
`waitForFunction` that ran out of time): the behaviour is missing. A web server that did not start, a page that could
not be reached (`page.goto` refused or timed out), a spec that does not parse or import, and a run that found no
test are wrong reasons: the suite said nothing about the behaviour and would never pass as it stands. Standard
library only; imported by scripts/tests_failing_first.py and scripts/verify.py.
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

#: ` FAIL  tests/a.test.ts > limiter > blocks after 100` and the suite-level ` FAIL  tests/a.test.ts [ tests/a.test.ts ]`.
#: A config with named projects (vitest `test.projects` / workspaces) prefixes both with the project: ` FAIL  |react| tests/a.test.ts > ...`.
VT_PROJECT = r"(?:\|(?P<project>[^|\n]+)\|\s+)?"
VT_FAIL = re.compile(r"^\s*FAIL\s+" + VT_PROJECT + r"(?P<id>\S+(?: > .+?)?)(?:\s+\[ (?P<suite>[^\]]+) \])?\s*$")
#: ` ❯ tests/a.test.ts (8 tests | 7 failed) 8ms`, ` ✓ tests/b.test.ts (3 tests) 2ms`, ` ❯ |react| tests/a.test.ts (12 tests | 12 failed) 7ms`
VT_FILE = re.compile(r"^\s*[❯✓×↓]\s+" + VT_PROJECT + r"(?P<file>\S+)\s+\((?P<n>\d+) tests?(?: \| (?P<rest>[^)]*))?\)")
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


# ------------------------------------------------------------------------------------------------------ playwright

#: list reporter, one line per finished test: `  ✘  1 [chromium] › tests/e2e/x.spec.ts:37:5 › suite › title (4.2s)`;
#: `✓` passed, `-` skipped (no duration), `ok` / `x` where the terminal has no UTF-8 (Windows); a retry adds `(retry #1)`.
PW_STATUS = re.compile(r"^\s*(?P<mark>✓|✘|ok|x|-)\s+(?P<n>\d+)\s+(?P<title>\S.*?)\s*(?:\((?P<dur>[\d.]+m?s)\))?\s*$")
#: the numbered failure header of the epilogue: `  1) [chromium] › tests/e2e/x.spec.ts:37:5 › title ──────` (no dashes
#: when the title fills the line); the same titles come back unnumbered under `  N failed`, which PW_TITLE still reads.
PW_FAILURE = re.compile(r"^\s*(?P<n>\d+)\) (?P<title>\S.*?)\s*─*\s*$")
#: `[chromium] › tests/e2e/x.spec.ts:37:5 › suite › title` -> project, file (relative to the cwd), line, the › path.
PW_TITLE = re.compile(r"^(?:\[(?P<project>[^\]]+)\] › )?(?P<file>[^\s›]+?):(?P<line>\d+):(?P<col>\d+) › (?P<names>.+?)(?: \(retry #\d+\))?$")
#: the summary: `  3 failed`, `  1 passed (29.5s)`, `  1 skipped`, `  2 flaky`, `  1 did not run`, `  1 interrupted`
PW_SUMMARY = re.compile(r"^\s{0,2}\d+ (?:failed|passed|skipped|flaky|did not run|interrupted)\b")
PW_NOT_PART = re.compile(r"^\s{0,2}\d+ errors? (?:was|were) not a part of any test")
PW_RUNNING = re.compile(r"^Running \d+ tests? using \d+ workers?")
#: an error of the run itself, printed at column 0 before any test: a spec that does not load, the web server, no tests
PW_TOP_ERROR = re.compile(r"^(?P<type>[A-Z]\w*Error|Error)(?:\s*\[[\w-]+\])?: (?P<msg>.*)$")
#: the first line of a test's failure: `Error: expect(locator).toHaveCount(expected) failed`, `TimeoutError: locator.click:
#: Timeout 1500ms exceeded.`, `ReferenceError: x is not defined`; a timed-out test starts with `Test timeout of Nms exceeded.`
PW_ERR = re.compile(r"^(?P<type>[A-Z]\w*Error|Error)(?:\s*\[[\w-]+\])?:\s?(?P<msg>.*)$")
PW_TEST_TIMEOUT = re.compile(r"^Test timeout of \d+ms exceeded\.")
#: a Playwright call names itself first: `page.goto: net::ERR_CONNECTION_REFUSED at ...`, `locator.click: Timeout 1500ms
#: exceeded.`, `page.waitForFunction: Test timeout of 20000ms exceeded.`, `expect(locator).toBeVisible() failed`
PW_CALL = re.compile(r"^(?P<call>(?:page|frame|locator|frameLocator|browser|browserContext|context|request|response|apiRequestContext|mouse|keyboard|download|worker)(?:\.\w+)+):\s*(?P<rest>.*)$", re.S)
PW_NAVIGATION = re.compile(r"^(?:page|frame)\.(?:goto|reload|goBack|goForward)$")
PW_IN_PAGE = re.compile(r"^(?:page|frame|locator|jsHandle|elementHandle|worker)\.(?:evaluate|evaluateHandle|evaluateAll|\$eval|\$\$eval|addInitScript|exposeFunction)$")
PW_EXPECT = re.compile(r"^expect\(|^Timed out \d+ms waiting for expect\(")
PW_WEBSERVER = re.compile(r"config\.webServer|is already used, make sure that nothing is running on the port")
PW_NO_TESTS = re.compile(r"^No tests found\b")
#: the test compared an HTTP status: the code under the failing line, or Expected/Received that are both status codes
PW_STATUS_CMP = re.compile(r"\.status\(\)|\bstatusCode\b|\.toBeOK\(\)|\btoHaveStatus\b|\bstatus_code\b"
                           r"|Expected:\s*[1-5]\d\d\b[\s\S]{0,80}Received:\s*[1-5]\d\d\b")
#: a file named in a load error: `SyntaxError: /x/tests/a.spec.ts: Unexpected token`, `Require stack:\n- /x/tests/a.spec.ts`
PW_PATH = re.compile(r"(?P<path>(?:/|\.{1,2}/|[\w@.-]+/)?[\w@./-]*?[\w@-]+\.(?:spec|test)\.[cm]?[jt]sx?)(?::\d+)?\b")
PW_STATUSES_RAN = ("passed", "failed", "timedOut", "interrupted")


def _pw_block_text(message: str, block: str) -> str:
    return f"{message}\n{block}"


def classify_playwright(message: str, block: str = "", status: str = "failed") -> str:
    """Reason for one Playwright test failure: `message` is its first error line, `block` the rest of what the reporter
    printed for it (the other errors of a timed-out test, the code snippet), `status` the result status (`failed`,
    `timedOut`, `interrupted`). Intended: a failed expectation, an element or page state that never came, an error raised in
    the page under test, a `not implemented` throw. Wrong: the web server, a page that could not be reached, a spec that
    does not parse or import, a name or attribute error in the test itself, a run that found no test."""
    first = message.strip()
    whole = _pw_block_text(first, block)
    if status == "interrupted":
        return "collection_error"
    if VT_NOT_IMPLEMENTED.search(first) or first.startswith("NotImplementedError"):
        return "not_implemented"
    m = PW_ERR.match(first)
    etype, msg = (m.group("type"), m.group("msg").strip()) if m else ("", first)
    if PW_WEBSERVER.search(whole) or PW_NO_TESTS.match(msg) or VT_COLLECT.search(msg):
        return "collection_error"
    if VT_IMPORT.search(whole) or etype == "ModuleNotFoundError":
        return "import_error"
    if etype == "SyntaxError" or VT_SYNTAX.search(msg):
        return "syntax_error"
    call = PW_CALL.match(msg)
    if call and PW_NAVIGATION.match(call.group("call")):
        return "collection_error"  # the page was never reached: the test said nothing about the behaviour
    if call and PW_IN_PAGE.match(call.group("call")):
        return "other"  # the error happened in the page under test (window.__hook undefined, say): the page lacks it
    if etype == "ReferenceError":
        return "name_error"
    if etype == "TypeError" and VT_ATTR.search(msg):
        return "attribute_error"
    if PW_EXPECT.match(msg) or etype == "AssertionError":
        return "status_mismatch" if PW_STATUS_CMP.search(whole) else "assertion"
    if PW_TEST_TIMEOUT.match(first) or status == "timedOut":
        inner = None
        for line in block.splitlines():
            em = PW_ERR.match(line.strip())
            cm = em and PW_CALL.match(em.group("msg").strip())
            if cm:
                inner = cm.group("call")
                break
        if inner is None:
            return "other"
        if PW_NAVIGATION.match(inner):
            return "collection_error"
        return "other" if PW_IN_PAGE.match(inner) else "assertion"  # waited for a state that never came
    if etype == "TimeoutError" or (call and re.match(r"Timeout \d+ms exceeded", call.group("rest"))):
        return "assertion"  # waiting for an element the page does not have yet
    return "other"


def _pw_split_title(title: str) -> tuple[str, str, str] | None:
    """(project, file, test id) of a list-reporter title; the id is `file > describe > test`."""
    m = PW_TITLE.match(title.strip())
    if not m:
        return None
    names = [part.strip() for part in m.group("names").split(" › ")]
    return (m.group("project") or ""), m.group("file"), " > ".join([m.group("file"), *names])


def _pw_top_error_rows(errors: list[dict], root: str | None) -> tuple[list[dict], list[dict]]:
    """Run-level errors split into rows for the spec files they name and the rest (the web server, no tests found)."""
    rows: list[dict] = []
    rest: list[dict] = []
    for err in errors:
        detail = err["detail"]
        reason = classify_playwright(detail, err.get("block", ""))
        if reason not in WRONG:
            reason = "collection_error"  # whatever stopped the file from loading, no test in it ran
        file = err.get("file")
        if not file:
            pm = PW_PATH.search(detail) or PW_PATH.search(err.get("block", ""))
            file = pm.group("path") if pm else None
        if file and root and posixpath.isabs(file):
            file = rel_to_root(file, root)
        if file and not PW_WEBSERVER.search(detail) and not PW_NO_TESTS.match(PW_ERR.match(detail).group("msg").strip() if PW_ERR.match(detail) else ""):
            row = {"test": file, "file": file, "reason": reason, "detail": detail}
            if reason == "import_error":
                im = VT_IMPORT.search(f"{detail}\n{err.get('block', '')}")
                mod = im and (im.group("mod") or im.group("mod2"))
                if mod:
                    row["module"] = mod
            rows.append(row)
        else:
            rest.append({"detail": detail, "reason": reason})
    return rows, rest


def parse_playwright_list(output: str, root: str | None = None) -> dict:
    """{"results", "passed", "skipped", "errors"} from the `list` reporter (the console's default)."""
    lines = [ANSI.sub("", ln).rstrip() for ln in output.splitlines()]
    passed: dict[str, int] = {}
    skipped: dict[str, int] = {}
    final_mark: dict[str, tuple[str, str]] = {}  # title -> (mark, file); a retry's later line wins
    sections: list[tuple[str, str, str]] = []  # (title, headline, block)
    top_errors: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        sm = PW_STATUS.match(line)
        if sm and _pw_split_title(sm.group("title")):
            key = re.sub(r" \(retry #\d+\)$", "", sm.group("title").strip())  # the last attempt's mark counts
            final_mark[key] = (sm.group("mark"), _pw_split_title(sm.group("title"))[1])
            i += 1
            continue
        fm = PW_FAILURE.match(line)
        if fm and _pw_split_title(fm.group("title")):
            j = i + 1
            block: list[str] = []
            while j < n and not (PW_FAILURE.match(lines[j]) and _pw_split_title(PW_FAILURE.match(lines[j]).group("title"))) \
                    and not PW_SUMMARY.match(lines[j]) and not PW_NOT_PART.match(lines[j]):
                block.append(lines[j])
                j += 1
            body = [b.strip() for b in block if b.strip()]
            headline = body[0] if body else ""
            sections.append((fm.group("title").strip(), headline, "\n".join(b.strip() for b in block)))
            i = j
            continue
        tm = PW_TOP_ERROR.match(line)
        if tm:
            j = i + 1
            block = []
            while j < n and not PW_TOP_ERROR.match(lines[j]) and not PW_RUNNING.match(lines[j]) and not PW_STATUS.match(lines[j]) \
                    and not PW_SUMMARY.match(lines[j]):
                block.append(lines[j])
                j += 1
            top_errors.append({"detail": line.strip(), "block": "\n".join(block)})
            i = j
            continue
        i += 1
    for title, (mark, file) in final_mark.items():
        if mark in ("✓", "ok"):
            passed[file] = passed.get(file, 0) + 1
        elif mark == "-":
            skipped[file] = skipped.get(file, 0) + 1
    projects = {(_pw_split_title(t) or ("",))[0] for t, _, _ in sections} | {(_pw_split_title(t) or ("",))[0] for t in final_mark}
    many = len({p for p in projects if p}) > 1
    results: list[dict] = []
    seen: set[str] = set()
    for title, headline, block in sections:
        mark = final_mark.get(title, ("✘", ""))[0]
        project, file, tid = _pw_split_title(title)
        if many and project:
            tid = f"{tid} [{project}]"
        if tid in seen or mark in ("✓", "ok"):
            continue  # a retry that passed in the end is not failing first; one row per test
        seen.add(tid)
        status = "timedOut" if PW_TEST_TIMEOUT.match(headline) else "failed"
        reason = classify_playwright(headline, block, status)
        row = {"test": tid, "file": file, "reason": reason, "detail": headline}
        if reason == "import_error":
            im = VT_IMPORT.search(f"{headline}\n{block}")
            mod = im and (im.group("mod") or im.group("mod2"))
            if mod:
                row["module"] = mod
        results.append(row)
    rows, rest = _pw_top_error_rows(top_errors, root)
    return {"results": results + rows, "passed": passed, "skipped": skipped, "errors": rest}


def parse_playwright_report(report: dict, root: str | None = None) -> dict:
    """{"results", "passed", "skipped", "errors"} from the `json` reporter's report (PLAYWRIGHT_JSON_OUTPUT_FILE).
    Files come back relative to `root` (the project) when given, else as the report spells them under its rootDir."""
    root_dir = str((report.get("config") or {}).get("rootDir") or "")

    def rel(file: str) -> str:
        full = file if posixpath.isabs(file) else posixpath.join(root_dir, file) if root_dir else file
        return rel_to_root(full, root) if root and posixpath.isabs(full) else full

    passed: dict[str, int] = {}
    skipped: dict[str, int] = {}
    results: list[dict] = []
    projects = {t.get("projectName") or "" for s in report.get("suites") or [] for sp in _pw_specs(s) for t in sp.get("tests") or []}
    many = len({p for p in projects if p}) > 1
    for suite in report.get("suites") or []:
        for spec, names in _pw_specs_with_path(suite, []):
            file = rel(spec.get("file") or suite.get("file") or "")
            for test in spec.get("tests") or []:
                runs = test.get("results") or []
                last = runs[-1] if runs else {}
                status = last.get("status") or test.get("status") or ""
                expected = test.get("expectedStatus") or "passed"
                if status == "passed" or (status == expected and status in PW_STATUSES_RAN):
                    passed[file] = passed.get(file, 0) + 1
                    continue
                if status == "skipped" or not runs:
                    skipped[file] = skipped.get(file, 0) + 1
                    continue
                messages = [ANSI.sub("", (e.get("message") or "")).strip() for e in last.get("errors") or [] if e.get("message")]
                main = last.get("error") or {}
                headline_src = ANSI.sub("", main.get("message") or (messages[0] if messages else "")).strip()
                headline = headline_src.splitlines()[0] if headline_src else f"{status} without an error message"
                block_parts = [headline_src] + [m for m in messages if m != headline_src] + [ANSI.sub("", main.get("snippet") or "")]
                block = "\n".join(p for p in block_parts if p)
                reason = classify_playwright(headline, block, status)
                tid = " > ".join([file, *names, spec.get("title") or ""])
                if many and test.get("projectName"):
                    tid = f"{tid} [{test['projectName']}]"
                row = {"test": tid, "file": file, "reason": reason, "detail": headline}
                if reason == "import_error":
                    im = VT_IMPORT.search(block)
                    mod = im and (im.group("mod") or im.group("mod2"))
                    if mod:
                        row["module"] = mod
                results.append(row)
    top = []
    for err in report.get("errors") or []:
        message = ANSI.sub("", err.get("message") or "").strip()
        loc = err.get("location") or {}
        top.append({"detail": message.splitlines()[0] if message else "error", "block": message,
                    "file": rel(loc["file"]) if loc.get("file") else None})
    rows, rest = _pw_top_error_rows(top, root)
    return {"results": results + rows, "passed": passed, "skipped": skipped, "errors": rest}


def rel_to_root(full: str, root: str) -> str:
    """`full` relative to `root`; symlinks resolved on both sides when the plain form climbs out (a report written from
    /private/var while the root was given as /var, say). An unrelated path comes back unchanged."""
    import os

    try:
        rel = os.path.relpath(full, root)
        if rel.startswith(".."):
            rel = os.path.relpath(os.path.realpath(full), os.path.realpath(root))
    except ValueError:
        return full
    return full if rel.startswith("..") else rel.replace(os.sep, "/")


def _pw_specs(suite: dict) -> list[dict]:
    return [spec for spec, _ in _pw_specs_with_path(suite, [])]


def _pw_specs_with_path(suite: dict, path: list[str]) -> list[tuple[dict, list[str]]]:
    """Every spec under a suite with the describe titles above it (the file suite's own title is its path, not a name)."""
    out: list[tuple[dict, list[str]]] = []
    for spec in suite.get("specs") or []:
        out.append((spec, path))
    for child in suite.get("suites") or []:
        out += _pw_specs_with_path(child, path + [child.get("title") or ""])
    return out


def find_report(output: str) -> dict | None:
    """The `json` reporter's report when it was printed to stdout (no output file): the first `{` line that decodes
    to an object with `suites` and `stats`."""
    import json

    text = ANSI.sub("", output)
    for m in re.finditer(r"^\{", text, re.M):
        try:
            data, _ = json.JSONDecoder().raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(data, dict) and "suites" in data and "stats" in data:
            return data
    return None


def parse_playwright(output: str, report: dict | None = None, root: str | None = None) -> dict:
    """{"results": [{test, file, reason, detail, module?}], "passed": {file: n}, "skipped": {file: n}, "errors": [{detail,
    reason}]} from a `playwright test` run: the JSON report when given (or printed in `output`), else the list reporter's
    text. `errors` are the run's own errors that name no spec file (the web server, no tests found): the caller attaches
    them to the files nothing ran from."""
    data = report if report is not None else find_report(output)
    if data is not None:
        return parse_playwright_report(data, root)
    return parse_playwright_list(output, root)


PW_IMPORT = re.compile(r"""(?:from\s+|require\(\s*)["']@playwright/test["']""")


def runner_of_file(path: str) -> str | None:
    """`playwright` for a spec that imports @playwright/test (it runs through the `e2e` block, not the stack's test
    command), None for any other test file (the stack's runner)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(20000)
    except OSError:
        return None
    return "playwright" if PW_IMPORT.search(head) else None


# ---------------------------------------------------------------------------------------------------------- shared

def detect_runner(command: str) -> str | None:
    """`pytest`, `vitest` or `playwright` for a test command, None when no shape is known. `pnpm test:e2e` and friends
    name Playwright (the e2e script of a Node project); the check runs before vitest's, whose `test` would match it."""
    if re.search(r"\bpytest\b|\bpy\.test\b", command):
        return "pytest"
    if re.search(r"\bplaywright\b|\btest:e2e\b|\be2e\b", command):
        return "playwright"
    if re.search(r"\bvitest\b|\b(?:pnpm|npm|yarn|bun)\b(?:\s+run)?\s+test\b", command):
        return "vitest"
    return None


def parse(output: str, runner: str, report: dict | None = None, root: str | None = None) -> dict:
    if runner == "pytest":
        return parse_pytest(output)
    if runner == "vitest":
        return parse_vitest(output)
    if runner == "playwright":
        return parse_playwright(output, report, root)
    raise ValueError(f"unknown runner {runner!r}; supported: pytest, vitest, playwright")


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
    if r == "collection_error" and (PW_WEBSERVER.search(row.get("detail", "")) or re.search(
            r"net::ERR_|ECONNREFUSED|page\.(?:goto|reload)", row.get("detail", ""))):
        return ("the app the spec drives was not reachable (the web server did not start, or the page never loaded): "
                "set the `e2e` block of .claude/sdlc.json (build, serve, url) so the gate starts the built app itself, "
                "or start the app at that url and rerun")
    if r == "collection_error":
        return "the runner could not collect the file (an error at import time, or no test in it): fix the test"
    return ""
