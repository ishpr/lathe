#!/usr/bin/env python3
"""Foldkit frontend contract: detection, five ratchet checkers, oxlint and the verify summary.

A Foldkit app is a set of programs (`src/programs/<page>/`): one Model (an effect Schema), a Message
union from `defineMessageUnion`, a pure `update`, Commands as values (`Command.define`, whose
`execute` reaches the backend) and views that return Html. Story tests (`*.story.test.ts`,
`story.test.ts`) drive `update`; scene tests (`*.scene.test.ts`, `scene.test.ts`) drive the view
through role, label, placeholder and text locators. That shape makes the frontend contract checkable
by text, so every checker here is regex and bracket matching over TypeScript (stdlib only, no node):
comments are blanked first and string contents are masked for bracket matching, so formatting
(prettier or not, trailing commas, line breaks) does not change the result.

Config (`.claude/sdlc.json`, written by init when a package.json depends on `foldkit`):
  "frontend": {"framework": "foldkit", "programs": ["src/programs/**"],
               "api_table": "src/lib/api/endpoints.ts", "oxlint": true}
plus the checker names below in "checkers". Program folders whose name starts with "_" (`_template`,
`_ui` helpers) are shared code, not programs, and are skipped.

Checkers (ratchet keys, line-number free like the rest of the pack):
  fe_story     fe-story|<message file>|<Tag>      a Message tag no story test of its program sends
  fe_scene     fe-scene|<view file>|<locator>     an interactive element a scene can reach only by text()
               fe-scene|<scene file>|no-toExist   a scene file that never asserts expect(...).toExist()
  fe_api       fe-api|<METHOD PATH>               a Command's execute calls a URL the api table lacks
               fe-api|~<METHOD PATH>              the same for a templated URL (low confidence)
  fe_secret    fe-secret|<model file>|<field>     a Model field named like a credential, not Redacted
  fe_devtools  fe-devtools|<file>                 DevTools exposed in production (no development check)
oxlint (frontend.oxlint): lint|<file>|<plugin>/<rule>, like eslint-json.

CLI: frontend_foldkit.py --check fe_story --root DIR [--config PATH]   (findings: key<TAB>file:line: message)
     frontend_foldkit.py --summary --root DIR                          (the verify stamp's frontend block, JSON)
     frontend_foldkit.py --detect --root DIR                           (the frontend block init would write)
Exit: --check 0 none, 1 findings, 2 misuse.
"""

from __future__ import annotations

import bisect
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # python3 -I ignores PYTHONDONTWRITEBYTECODE; importing the pack's modules must not leave __pycache__ in the pack
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sdlc as cl  # noqa: E402

FRAMEWORK = "foldkit"
DEFAULTS = {"framework": FRAMEWORK, "programs": ["src/programs/**"], "api_table": "src/lib/api/endpoints.ts", "oxlint": True}
# checker name (config "checkers") -> key prefix
CHECKERS = {"fe_story": "fe-story", "fe_scene": "fe-scene", "fe_api": "fe-api", "fe_secret": "fe-secret", "fe_devtools": "fe-devtools"}
TS_EXTS = [".ts", ".tsx", ".mts", ".cts"]
SRC_EXTS = TS_EXTS + [".js", ".jsx", ".mjs", ".cjs"]
SKIP_PARTS = {"node_modules", ".next", "coverage", "dist", "build", "out"}


# ---------- source scanning ----------

_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "yield", "await", "void", "delete", "instanceof"}


class Source:
    """One file: `code` has comments blanked, `blank` additionally masks string, template and regex
    contents (the quotes stay), so brackets and commas found in `blank` are real syntax. All three
    strings have the same length, so a position means the same thing in each."""

    def __init__(self, rel: str, text: str) -> None:
        self.rel = rel
        self.text = text
        self.code, self.blank = _scan(text)
        self._nl = [i for i, ch in enumerate(text) if ch == "\n"]

    def line(self, pos: int) -> int:
        return bisect.bisect_left(self._nl, pos) + 1

    def line_text(self, pos: int) -> str:
        start = self.text.rfind("\n", 0, pos) + 1
        end = self.text.find("\n", pos)
        return self.text[start:end if end >= 0 else len(self.text)]


def _scan(src: str) -> tuple[str, str]:
    n = len(src)
    code = list(src)
    blank = list(src)

    def wipe(a: int, b: int, both: bool) -> None:
        for k in range(a, min(b, n)):
            if src[k] != "\n":
                blank[k] = " "
                if both:
                    code[k] = " "

    depth = 0
    tpl: list[int] = []
    mode = "code"
    i = 0
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if mode == "tpl":
            if c == "\\":
                wipe(i, i + 2, False)
                i += 2
            elif c == "`":
                mode = "code"
                i += 1
            elif c == "$" and nxt == "{":
                wipe(i, i + 2, False)
                tpl.append(depth)
                mode = "code"
                i += 2
            else:
                wipe(i, i + 1, False)
                i += 1
            continue
        if c == "/" and nxt == "/":
            end = src.find("\n", i)
            end = n if end < 0 else end
            wipe(i, end, True)
            i = end
            continue
        if c == "/" and nxt == "*":
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            wipe(i, end, True)
            i = end
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            wipe(i + 1, j, False)
            i = j + 1
            continue
        if c == "`":
            mode = "tpl"
            i += 1
            continue
        if c == "/" and _regex_starts(src, i):
            j = i + 1
            in_class = False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and src[j] == "/":
                wipe(i + 1, j, False)
                i = j + 1
                continue
        if c == "{":
            depth += 1
        elif c == "}":
            if tpl and tpl[-1] == depth:
                tpl.pop()
                wipe(i, i + 1, False)
                mode = "tpl"
                i += 1
                continue
            depth -= 1
        i += 1
    return "".join(code), "".join(blank)


def _regex_starts(src: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    if j < 0 or src[j] in _REGEX_PREV:
        return True
    if src[j].isalnum() or src[j] in "_$":
        k = j
        while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
            k -= 1
        return src[k + 1:j + 1] in _REGEX_WORDS
    return False


_PAIRS = {"(": ")", "[": "]", "{": "}"}


def close_of(blank: str, i: int) -> int:
    """Index of the bracket that closes the one at i, or -1."""
    stack = []
    for k in range(i, len(blank)):
        ch = blank[k]
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif ch in ")]}":
            if not stack or stack.pop() != ch:
                return -1
            if not stack:
                return k
    return -1


def split_top(blank: str, a: int, b: int) -> list[tuple[int, int]]:
    """Top-level comma-separated spans of blank[a:b], empty spans dropped."""
    out = []
    depth = 0
    start = a
    for k in range(a, b):
        ch = blank[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append((start, k))
            start = k + 1
    out.append((start, b))
    return [(s, e) for s, e in out if blank[s:e].strip()]


def call_args(src: Source, paren: int) -> tuple[list[tuple[int, int]], int]:
    close = close_of(src.blank, paren)
    if close < 0:
        return [], -1
    return split_top(src.blank, paren + 1, close), close


_KEY_RE = re.compile(r"\s*(?:(['\"])([^'\"\n]+)\1|([A-Za-z_$][\w$]*))\s*(:|\(|$)")


def object_keys(src: Source, brace: int) -> list[tuple[str, int, int, int]]:
    """(key, key position, value start, value end) for each top-level property of the object at brace."""
    close = close_of(src.blank, brace)
    if close < 0:
        return []
    out = []
    for s, e in split_top(src.blank, brace + 1, close):
        seg = src.code[s:e]
        if seg.lstrip().startswith("..."):
            continue
        m = _KEY_RE.match(seg)
        if not m:
            continue
        key = m.group(2) or m.group(3)
        vstart = s + m.end() if m.group(4) == ":" else e
        out.append((key, s + m.start(2 if m.group(2) else 3), vstart, e))
    return out


def string_literals(src: Source, a: int, b: int):
    """(content, start, is_template_with_expressions) for each string literal in code[a:b]."""
    k = a
    while k < b:
        ch = src.code[k]
        if ch in "'\"`" and src.blank[k] == ch:
            end = k + 1
            while end < len(src.code):
                if src.code[end] == "\\":
                    end += 2
                    continue
                if src.code[end] == ch and src.blank[end] == ch:
                    break
                end += 1
            content = src.code[k + 1:end]
            yield content, k, ch == "`" and "${" in content
            k = end + 1
        else:
            k += 1


def enclosing_openers(blank: str, pos: int) -> list[int]:
    """Positions of the brackets that enclose pos, innermost last."""
    stack: list[int] = []
    for k in range(pos):
        ch = blank[k]
        if ch in "([{":
            stack.append(k)
        elif ch in ")]}" and stack:
            stack.pop()
    return stack


def head_before(src: Source, opener: int) -> str:
    """The expression text in front of an opening bracket, back to the previous `; , { }` at its level
    (`if (DEV) ` for `if (DEV) {`, `devTools: DEV ? ` for `devTools: DEV ? {`)."""
    depth = 0
    k = opener - 1
    while k >= 0:
        ch = src.blank[k]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in ";,":
            break
        k -= 1
    return src.code[k + 1:opener]


# ---------- files ----------

def frontend_of(cfg: dict) -> dict | None:
    fe = cfg.get("frontend")
    if not isinstance(fe, dict) or fe.get("framework") != FRAMEWORK:
        return None
    out = dict(DEFAULTS)
    out.update({k: v for k, v in fe.items() if v is not None})
    if isinstance(out.get("programs"), str):
        out["programs"] = [out["programs"]]
    return out


def program_bases(fe: dict) -> list[str]:
    """`src/programs/**` -> `src/programs`: the folder whose subfolders are programs."""
    bases = []
    for pat in fe.get("programs") or []:
        parts = []
        for seg in pat.strip("/").split("/"):
            if any(ch in seg for ch in "*?["):
                break
            parts.append(seg)
        bases.append("/".join(parts))
    return bases


def program_of(rel: str, bases: list[str]) -> str:
    for base in sorted(bases, key=len, reverse=True):
        prefix = base + "/" if base else ""
        if rel.startswith(prefix):
            rest = rel[len(prefix):].split("/")
            return prefix + rest[0] if len(rest) > 1 else base
    return os.path.dirname(rel)


def _skipped(rel: str, bases: list[str]) -> bool:
    if any(p in SKIP_PARTS for p in rel.split("/")):
        return True
    prog = program_of(rel, bases)
    return os.path.basename(prog).startswith("_") and prog not in bases


def is_story(rel: str) -> bool:
    base = os.path.basename(rel)
    return bool(re.fullmatch(r"(?:.+\.)?story\.test\.[cm]?tsx?", base))


def is_scene(rel: str) -> bool:
    base = os.path.basename(rel)
    return bool(re.fullmatch(r"(?:.+\.)?scene\.test\.[cm]?tsx?", base))


def is_test(rel: str) -> bool:
    return bool(re.search(r"\.(?:test|spec)\.[cm]?[jt]sx?$", rel)) or "/__tests__/" in "/" + rel


def package_dir(root: str, bases: list[str]) -> str:
    """The folder holding the frontend's package.json (repo-relative, "" for the root)."""
    cur = bases[0] if bases else ""
    while True:
        if os.path.isfile(os.path.join(root, cur, "package.json")):
            return cur
        if not cur:
            return ""
        cur = os.path.dirname(cur)


class Tree:
    """The programs of one frontend, read once."""

    def __init__(self, root: str, fe: dict) -> None:
        self.root = root
        self.fe = fe
        self.bases = program_bases(fe)
        self.files: dict[str, Source] = {}
        for rel in cl.walk_files(root, fe.get("programs") or [], TS_EXTS):
            if _skipped(rel, self.bases) or rel.endswith(".d.ts"):
                continue
            self.files[rel] = self._read(rel)
        self.sources = {r: s for r, s in self.files.items() if not is_test(r)}
        self.stories = {r: s for r, s in self.files.items() if is_story(r)}
        self.scenes = {r: s for r, s in self.files.items() if is_scene(r)}

    def _read(self, rel: str) -> Source:
        with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as fh:
            return Source(rel, fh.read())

    def package_dir(self) -> str:
        return package_dir(self.root, self.bases)

    def api_table(self) -> list[Source]:
        rel = (self.fe.get("api_table") or "").strip("/")
        if not rel:
            return []
        full = os.path.join(self.root, rel)
        if os.path.isdir(full):
            rels = cl.walk_files(self.root, [rel + "/**"], TS_EXTS)
        elif os.path.isfile(full):
            rels = [rel]
        else:
            return []
        return [self._read(r) for r in rels if not is_test(r)]


Finding = tuple[str, str]


# ---------- fe-story ----------

def message_tags(src: Source) -> list[tuple[str, int]]:
    """Tags of every defineMessageUnion({...}) in the file; in a message(s).ts file also the
    standalone `taggedStruct('Tag', ...)` / `Schema.TaggedStruct('Tag', ...)` members of a union."""
    tags = []
    for m in re.finditer(r"\bdefineMessageUnion\s*(?:<[^>]*>)?\s*\(\s*\{", src.blank):
        for key, kpos, _, _ in object_keys(src, m.end() - 1):
            tags.append((key, kpos))
    if re.fullmatch(r"messages?\.[cm]?tsx?", os.path.basename(src.rel)):
        for m in re.finditer(r"\b(?:taggedStruct|Schema\s*\.\s*TaggedStruct)\s*\(\s*(['\"])", src.blank):
            q = m.end() - 1
            end = src.code.find(src.code[q], q + 1)
            if end > q:
                tags.append((src.code[q + 1:end], q + 1))
    return tags


def check_story(tree: Tree) -> tuple[list[Finding], dict]:
    """Rule: every Message tag is sent by a story test of its program. A Message is the only way state
    changes, so a tag that no story sends is a state transition with no executable acceptance.
    Evidence: `<file>:<line>: Message tag <Tag> is not sent by any story test in <program>`.
    Covered = the tag is called (`Tag(` or `Message.Tag(`) in a `*.story.test.ts` / `story.test.ts`
    of the same program folder (a message file directly in the programs folder: any story test).
    Key: fe-story|<message file>|<Tag>."""
    findings: list[Finding] = []
    total = covered = 0
    for rel, src in sorted(tree.sources.items()):
        tags = message_tags(src)
        if not tags:
            continue
        prog = program_of(rel, tree.bases)
        scope = [s for r, s in tree.stories.items() if prog in tree.bases or r.startswith(prog + "/")]
        for tag, pos in tags:
            total += 1
            pat = re.compile(r"(?<![\w$])" + re.escape(tag) + r"\s*\(")
            if any(pat.search(s.code) for s in scope):
                covered += 1
                continue
            where = f"{len(scope)} story test(s)" if scope else "no story test"
            findings.append((f"fe-story|{rel}|{tag}",
                             f"{rel}:{src.line(pos)}: Message tag {tag} is not sent by any story test in {prog} ({where}); "
                             f"add a story that sends it: Story.message({tag}(...)) or Story.Command.resolve(Cmd, {tag}(...))"))
    return findings, {"tags": total, "covered": covered}


# ---------- fe-scene ----------

HTML_TAGS = set("""a abbr address article aside audio b blockquote body br button canvas caption cite code col colgroup
data datalist dd del details dfn dialog div dl dt em fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup
hr i iframe img input ins kbd label legend li main mark menu meter nav ol optgroup option output p picture pre progress q s
samp search section select small span strong sub summary sup table tbody td template textarea tfoot th thead time tr u ul
var video""".split())
NATIVE = {"button", "a", "input", "select", "textarea"}
_ATTR_RE = re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)?(OnClick|Role|AriaLabel|AriaLabelledBy|Id|For|Href|OnInput|OnChange)\s*\(")


def builder_names(src: Source) -> tuple[set[str], set[str]]:
    """(qualifiers, bare element names): `h.button(` when h is an HtmlBuilder, `button(` when
    destructured from one."""
    quals = {"h", "html"} | set(re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*HtmlBuilder\b", src.code))
    bare: set[str] = set()
    for m in re.finditer(r"\{([^{}]*)\}\s*(?::\s*HtmlBuilder\b[^=,)]*)?(?:=\s*([A-Za-z_$][\w$]*))?", src.blank):
        if m.group(2) in quals or (m.group(0).find("HtmlBuilder") >= 0):
            for part in m.group(1).split(","):
                name = part.split(":")[-1].strip()
                if name in HTML_TAGS:
                    bare.add(name)
    return quals, bare


def element_calls(src: Source):
    """(tag, name position, open paren) for each Html element call in the file."""
    quals, bare = builder_names(src)
    for m in re.finditer(r"(?<![\w$.])(?:([A-Za-z_$][\w$]*)\s*\.\s*)?([a-z][a-z0-9]*)\s*\(", src.blank):
        qual, tag = m.group(1), m.group(2)
        if tag not in HTML_TAGS:
            continue
        if qual is not None and qual not in quals:
            continue
        if qual is None and tag not in bare:
            continue
        yield tag, m.start(2), m.end() - 1


def _first_literal(src: Source, a: int, b: int) -> str | None:
    for content, _, _ in string_literals(src, a, b):
        text = re.sub(r"\$\{[^}]*\}", "…", content)
        text = re.sub(r"[\s|\t]+", " ", text).strip()
        if text:
            return text[:40]
    return None


def check_scene(tree: Tree) -> tuple[list[Finding], dict]:
    """Rule: a scene test can reach every interactive element by role or label, and every scene file
    asserts something exists. Interactive = button, a, input, select, textarea, or any element with an
    OnClick handler. Reachable = a Role(...), AriaLabel(...) or AriaLabelledBy(...) attribute; an Id the
    file's label For(...) points at, or being inside a label element (form fields); a button with
    children, or an `a` with Href and children (role plus accessible name); attributes spread in from
    elsewhere (`...attributes.input`, as @foldkit/ui components hand them out) are given the benefit of
    the doubt. Anything else (a div with
    OnClick, an unlabelled input, an empty button) is reachable only by text() or not at all.
    Evidence: `<file>:<line>: <tag> ... reachable only by text(...)`; `<scene>:1: no expect(...).toExist()`.
    Keys: fe-scene|<view file>|<tag>:text(<text>) | <tag>:on(<Message>) | <tag>:#<n>, and
    fe-scene|<scene file>|no-toExist."""
    findings: list[Finding] = []
    for rel, src in sorted(tree.sources.items()):
        if "foldkit/html" not in src.code and "HtmlBuilder" not in src.code:
            continue
        fors = set(re.findall(r"(?<![\w$])(?:[\w$]+\s*\.\s*)?For\s*\(\s*['\"]([^'\"]+)['\"]", src.code))
        labels = []
        calls = list(element_calls(src))
        for tag, _, paren in calls:
            if tag == "label":
                end = close_of(src.blank, paren)
                if end > 0:
                    labels.append((paren, end))
        ordinal: dict[str, int] = {}
        for tag, npos, paren in calls:
            args, close = call_args(src, paren)
            if close < 0 or not args:
                continue
            a0, b0 = args[0]
            attrs_text = src.code[a0:b0]
            attrs = set(_ATTR_RE.findall(attrs_text)) if attrs_text.lstrip().startswith("[") else set()
            if tag not in NATIVE and "OnClick" not in attrs:
                continue
            if attrs & {"Role", "AriaLabel", "AriaLabelledBy"} or "..." in attrs_text:
                continue  # a spread (`...attributes.input` from a @foldkit/ui component) may carry the label
            ids = set(re.findall(r"(?<![\w$])(?:[\w$]+\s*\.\s*)?Id\s*\(\s*['\"]([^'\"]+)['\"]", attrs_text))
            in_label = any(a < paren < b for a, b in labels)
            if tag in ("input", "select", "textarea") and (ids & fors or in_label):
                continue
            children = src.blank[args[1][0]:args[1][1]].strip() if len(args) > 1 else ""
            has_children = bool(children) and children != "[]" and re.sub(r"[\s\[\]]", "", children) != ""
            if (tag == "button" or (tag == "a" and "Href" in attrs)) and has_children:
                continue
            text = _first_literal(src, args[1][0], args[1][1]) if len(args) > 1 else None
            on = re.search(r"(?:OnClick|OnInput|OnChange)\s*\(\s*(?:\(?[\w$,\s]*\)?\s*=>\s*)?(?:[\w$]+\s*\.\s*)?([A-Z][\w$]*)", attrs_text)
            if text:
                loc = f"{tag}:text({text})"
                how = f"reachable only by text({text!r})"
            elif on:
                loc = f"{tag}:on({on.group(1)})"
                how = "not reachable by any locator but text()" if has_children else "not reachable by role, label or text"
            else:
                ordinal[tag] = ordinal.get(tag, 0) + 1
                loc = f"{tag}:#{ordinal[tag]}"
                how = "not reachable by role, label or text"
            what = f"<{tag}> with an OnClick handler" if tag not in NATIVE else f"<{tag}>"
            findings.append((f"fe-scene|{rel}|{loc}",
                             f"{rel}:{src.line(npos)}: {what} has no role or label, so a scene test finds it {how}; "
                             "give it Role(...) and AriaLabel(...), use a button, or label the field (AriaLabel, or Id with a label For)"))
    with_exist = 0
    for rel, src in sorted(tree.scenes.items()):
        if re.search(r"\.\s*toExist\s*\(", src.code):
            with_exist += 1
            continue
        findings.append((f"fe-scene|{rel}|no-toExist",
                         f"{rel}:1: scene file has no expect(...).toExist(); assert the elements the scene reaches, by role or label"))
    return findings, {"files": len(tree.scenes), "with_exist": with_exist}


# ---------- fe-api ----------

_API_CALLEES = {"fetch", "get", "post", "put", "patch", "delete", "del", "head", "options", "request", "send"}
_METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "del": "DELETE", "head": "HEAD", "options": "OPTIONS"}


def url_path(content: str, templated: bool) -> tuple[str | None, bool]:
    """Normalised path of a URL literal (`{}` for every parameter, no origin, query or trailing slash),
    and whether it is low confidence (a template with expressions). None when it is not a URL."""
    s = content.strip()
    low = templated
    if templated and s.startswith("${"):
        end = s.find("}")
        rest = s[end + 1:] if end > 0 else ""
        if not rest.startswith("/"):
            return None, low
        s = rest
    m = re.match(r"https?://[^/\s]*", s)
    if m:
        s = s[m.end():] or "/"
    if not s.startswith("/") or s.startswith("//") or len(s) < 2:
        return None, low
    s = re.split(r"[?#]", s, maxsplit=1)[0]
    s = re.sub(r"\$\{[^}]*\}", "{}", s)
    segs = []
    for seg in s.split("/"):
        if seg.startswith(":") or (seg.startswith("{") and seg.endswith("}")):
            seg = "{}"
        segs.append(seg)
    return "/".join(segs).rstrip("/") or "/", low


def _same_path(a: str, b: str) -> bool:
    sa, sb = a.split("/"), b.split("/")
    return len(sa) == len(sb) and all(x == y or x == "{}" or y == "{}" for x, y in zip(sa, sb))


def _method_near(src: Source, lit: int, stop: int) -> str | None:
    before = src.code[max(0, lit - 80):lit]
    m = re.search(r"\.\s*(get|post|put|patch|delete|del|head|options)\s*\(\s*$", before, re.I)
    if m:
        return _METHODS[m.group(1).lower()]
    m = re.search(r"\bmethod\s*:\s*['\"](\w+)['\"]", src.code[lit:min(stop, lit + 300)])
    return m.group(1).upper() if m else None


def api_table_entries(tables: list[Source]) -> tuple[set[str], list[tuple[str | None, str]]]:
    """(exported function names, [(METHOD, path)]) of the api table. The method comes from a
    `method:` option or a `.post(` style call in the same export; none found means GET, as for fetch."""
    names: set[str] = set()
    entries: list[tuple[str | None, str]] = []
    for src in tables:
        names |= set(re.findall(r"\bexport\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)", src.code))
        for m in re.finditer(r"\bexport\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*", src.blank):
            names.add(m.group(1))
            if src.blank[m.end():m.end() + 1] == "{":
                names |= {k for k, _, _, _ in object_keys(src, m.end())}
        bounds = [m.start() for m in re.finditer(r"\bexport\b", src.blank)] + [len(src.code)]
        for content, pos, templated in string_literals(src, 0, len(src.code)):
            path, _ = url_path(content, templated)
            if path is None:
                continue
            k = bisect.bisect_right(bounds, pos)
            stop = bounds[k] if k < len(bounds) else len(src.code)
            start = bounds[k - 1] if k > 0 else 0
            method = _method_near(src, pos, stop)
            if method is None:
                mm = re.search(r"\bmethod\s*:\s*['\"](\w+)['\"]", src.code[start:stop]) or re.search(
                    r"\.\s*(get|post|put|patch|delete|del)\s*\(", src.code[start:stop], re.I)
                if mm:
                    method = _METHODS.get(mm.group(1).lower(), mm.group(1).upper())
            entries.append((method or "GET", path))
    return names, entries


def command_defs(src: Source):
    """(name, name position, execute start, execute end) for each Command.define in the file."""
    for m in re.finditer(r"\bCommand\s*\.\s*define\s*(?:<[^>]*>)?\s*\(", src.blank):
        args, close = call_args(src, m.end() - 1)
        if close < 0 or not args:
            continue
        name = _first_literal(src, *args[0]) or "?"
        a, b = args[1] if len(args) > 1 else args[0]
        brace = src.blank.find("{", a, b)
        span = (a, b)
        if brace >= 0 and not src.blank[a:brace].strip():
            for key, _, vs, ve in object_keys(src, brace):
                if key == "execute":
                    span = (vs, ve)
        yield name, m.start(), span[0], span[1]


def check_api(tree: Tree) -> tuple[list[Finding], dict]:
    """Rule: every backend call a Command makes goes through the api table (frontend.api_table). In
    each Command.define's execute: a call to a function the table exports is a matched call; a URL
    literal (starting with / or http, or `${base}/...`) passed to fetch / get / post / put / patch /
    delete / request (or a `url:` property) is a direct call, matched when the table holds that path
    (parameters match any segment; a table path with a known method must agree). A direct call the
    table lacks is a finding; a templated URL is reported with a "~" (low confidence).
    Evidence: `<file>:<line>: Command <Name> calls <METHOD> <path> ...`. Key: fe-api|<METHOD PATH>
    or fe-api|~<METHOD PATH>."""
    findings: list[Finding] = []
    tables = tree.api_table()
    table_rel = tree.fe.get("api_table") or "(no api_table)"
    names, entries = api_table_entries(tables)
    calls = matched = 0
    for rel, src in sorted(tree.sources.items()):
        if "Command" not in src.code:
            continue
        for name, npos, a, b in command_defs(src):
            body = src.blank[a:b]
            for fn in names:
                n = len(re.findall(r"(?<![\w$])" + re.escape(fn) + r"\s*\(", body))
                calls += n
                matched += n
            for content, pos, templated in string_literals(src, a, b):
                before = src.code[max(a, pos - 60):pos]
                callee = re.search(r"([A-Za-z_$][\w$]*)\s*\(\s*$", before)
                is_url_prop = re.search(r"\burl\s*:\s*$", before)
                if not ((callee and callee.group(1).lower() in _API_CALLEES) or is_url_prop):
                    continue
                path, low = url_path(content, templated)
                if path is None:
                    continue
                calls += 1
                method = _method_near(src, pos, b) or "GET"
                if any(_same_path(path, p) and (m is None or m == method) for m, p in entries):
                    matched += 1
                    continue
                key = f"fe-api|{'~' if low else ''}{method} {path}"
                why = f"no function in {table_rel} exposes it" if tables else f"the api table {table_rel} does not exist"
                findings.append((key, f"{src.rel}:{src.line(pos)}: Command {name} calls {method} {path} directly and {why}"
                                      f"{' (templated URL, low confidence)' if low else ''}; add an endpoint function to the table and call it from execute"))
    return findings, {"calls": calls, "matched": matched}


# ---------- fe-secret ----------

CRED_SEGMENTS = {"password", "passwd", "pwd", "secret", "token", "apikey", "authorization", "credentials", "credential"}
CRED_BENIGN_LAST = {"id", "ids", "name", "names", "count", "max", "min", "limit", "ttl", "type", "kind", "field", "header",
                    "url", "path", "file", "length", "len", "expires", "seconds", "visible", "shown", "error", "status"}
CRED_BENIGN_FIRST = {"max", "min", "num", "count", "n", "has", "is", "show"}


def cred_name(name: str) -> bool:
    """Whole-segment match (the pack's CRED-PARAM rule), camelCase aware: apiKey, accessToken,
    client_secret, password; not tokenCount, maxTokens, secretName, isPasswordVisible."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name))
    segs = [s for s in re.split(r"[_\-\s]+", spaced.lower()) if s]
    if len(segs) > 1 and (segs[-1] in CRED_BENIGN_LAST or segs[0] in CRED_BENIGN_FIRST):
        return False
    return any(s in CRED_SEGMENTS for s in segs) or any(a == "api" and b == "key" for a, b in zip(segs, segs[1:]))


def check_secret(tree: Tree) -> tuple[list[Finding], dict]:
    """Rule: the Model holds no credentials. DevTools records every Model and a connected DevTools MCP
    agent reads it, so a token in the Model leaks to anyone with the relay. A field whose name matches
    the pack's credential segments (password, passwd, pwd, secret, token, api key, authorization,
    credential[s]) in a Schema.Struct or defineTaggedUnion of a model file (model.ts, models.ts,
    *.model.ts, or a file that exports `Model`) is a finding unless its schema is Redacted
    (Schema.Redacted hides it from inspection; it does not encrypt it) or the line ends with
    `// secret: ok <reason>`. Evidence: `<file>:<line>: Model field <field> ...`.
    Key: fe-secret|<model file>|<field>."""
    findings: list[Finding] = []
    fields = 0
    for rel, src in sorted(tree.sources.items()):
        base = os.path.basename(rel)
        if not (re.fullmatch(r"(?:models?|.+\.model)\.[cm]?tsx?", base) or re.search(r"\bexport\s+const\s+Model\b", src.code)):
            continue
        objs = [m.end() - 1 for m in re.finditer(r"\bSchema\s*\.\s*Struct\s*\(\s*\{", src.blank)]
        for m in re.finditer(r"\bdefineTaggedUnion\s*\(\s*\{", src.blank):
            for _, _, vs, ve in object_keys(src, m.end() - 1):
                brace = src.blank.find("{", vs, ve)
                if brace >= 0 and not src.blank[vs:brace].strip():
                    objs.append(brace)
        seen = set()
        for brace in objs:
            for key, kpos, vs, ve in object_keys(src, brace):
                if kpos in seen:
                    continue
                seen.add(kpos)
                fields += 1
                if not cred_name(key):
                    continue
                if "Redacted" in src.code[vs:ve] or "secret: ok" in src.line_text(kpos):
                    continue
                findings.append((f"fe-secret|{rel}|{key}",
                                 f"{rel}:{src.line(kpos)}: Model field {key} looks like a credential; DevTools and the DevTools MCP "
                                 "record the Model, so keep it behind the server boundary, or make it Schema.Redacted, or end the line with '// secret: ok <reason>'"))
    return findings, {"fields": fields}


# ---------- fe-devtools ----------

DEV_CHECK = re.compile(
    r"(?<!!)import\.meta\.env\.DEV\b"
    r"|(?:import\.meta\.env\.MODE|process\.env\.NODE_ENV)\s*===?\s*['\"]development['\"]"
    r"|(?:import\.meta\.env\.MODE|process\.env\.NODE_ENV)\s*!==?\s*['\"]production['\"]"
    r"|\b__DEV__\b|\bisDev(?:elopment|Mode)?\b"
    r"|\bcommand\s*===?\s*['\"]serve['\"]|\bmode\s*===?\s*['\"]development['\"]"
)
_EXPOSURES = [
    (re.compile(r"\bshow\s*:\s*(['\"])Always\1"), "show: 'Always'", True),
    (re.compile(r"\bdevToolsMcpPort\s*:\s*\d+"), "a fixed devToolsMcpPort (the relay listens on every interface without a token)", False),
]


def _guarded(src: Source, pos: int) -> bool:
    """True when a development check sits in the property's own expression (from the previous comma
    at its level to the next one) or in the head of any enclosing bracket (an if condition, a ternary,
    `devTools: DEV ? {`)."""
    openers = enclosing_openers(src.blank, pos)
    inner = openers[-1] if openers else -1
    seg_start, depth = inner + 1, 0
    for k in range(pos - 1, inner, -1):
        ch = src.blank[k]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            depth -= 1
        elif ch == "," and depth == 0:
            seg_start = k + 1
            break
    close = close_of(src.blank, inner) if inner >= 0 else -1
    seg_end, depth = (close if close > 0 else len(src.blank)), 0
    for k in range(pos, seg_end):
        ch = src.blank[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            seg_end = k
            break
    if DEV_CHECK.search(src.code[seg_start:seg_end]):
        return True
    return any(DEV_CHECK.search(head_before(src, o)) for o in openers)


def check_devtools(tree: Tree) -> tuple[list[Finding], dict]:
    """Rule: DevTools stays a development tool. `show: 'Always'` in a devTools config mounts the overlay
    for production users, and a fixed `devToolsMcpPort` starts a relay that listens on every interface
    without a token; either one is a finding unless a development check guards it (import.meta.env.DEV,
    MODE/NODE_ENV compared with development or production, __DEV__, isDev, vite's command === 'serve')
    in the property's own expression or in the condition of an enclosing block, ternary or object.
    The oxlint rule no-disabling-dev-guardrails covers the reverse (guardrails switched off).
    Scans the frontend package (source and vite config, not tests). Evidence:
    `<file>:<line>: DevTools <what> is not behind a development check`. Key: fe-devtools|<file>."""
    findings: list[Finding] = []
    pkg = tree.package_dir()
    rels = cl.walk_files(tree.root, [pkg + "/**" if pkg else "**"], SRC_EXTS)
    scanned = 0
    for rel in rels:
        if is_test(rel) or rel.endswith(".d.ts") or any(p in SKIP_PARTS for p in rel.split("/")):
            continue
        src = tree.files.get(rel) or tree._read(rel)
        if "devTools" not in src.code and "devToolsMcpPort" not in src.code:
            continue
        scanned += 1
        for rx, what, needs_ctx in _EXPOSURES:
            for m in rx.finditer(src.code):
                if src.blank[m.start()] == " ":
                    continue  # inside a string or template
                if needs_ctx:
                    openers = enclosing_openers(src.blank, m.start())
                    if not openers or not re.search(r"\bdevTools\b", head_before(src, openers[-1])):
                        continue
                if _guarded(src, m.start()):
                    continue
                findings.append((f"fe-devtools|{rel}",
                                 f"{rel}:{src.line(m.start())}: DevTools {what} is not behind a development check, so production builds expose it; "
                                 "wrap it: devTools: import.meta.env.DEV ? {...} : false"))
    return findings, {"files": scanned}


CHECKS = {"fe_story": check_story, "fe_scene": check_scene, "fe_api": check_api, "fe_secret": check_secret, "fe_devtools": check_devtools}


# ---------- oxlint ----------

def oxlint_rule(code: str) -> str:
    """`foldkit(no-module-level-mutable-state)` -> `foldkit/no-module-level-mutable-state`."""
    m = re.fullmatch(r"\s*([\w@/.-]+)\((.+)\)\s*", code or "")
    return f"{m.group(1)}/{m.group(2)}" if m else (code or "unknown")


def parse_oxlint(out: str, root: str, pkg: str) -> tuple[list[Finding], str | None]:
    text = out.strip()
    if not text:
        return [], "oxlint printed nothing"
    try:
        data = json.loads(text[text.find("{"):] if not text.startswith(("{", "[")) else text)
    except ValueError as exc:
        return [], f"oxlint output not parsed: {exc}"
    diags = data.get("diagnostics", []) if isinstance(data, dict) else data
    findings: list[Finding] = []
    for d in diags or []:
        if not isinstance(d, dict):
            continue
        fname = d.get("filename") or "?"
        rel = cl.rel_to(root, fname) if os.path.isabs(fname) else os.path.normpath(os.path.join(pkg, fname)).replace(os.sep, "/")
        rule = oxlint_rule(d.get("code") or "")
        labels = d.get("labels") or [{}]
        line = ((labels[0] or {}).get("span") or {}).get("line", 0)
        findings.append((f"lint|{rel}|{rule}", f"{rel}:{line}: {rule} {d.get('message', '')}".rstrip()))
    return findings, None


def oxlint_command(root: str, pkg: str, targets: list[str]) -> tuple[list[str] | None, str | None, str | None]:
    """(argv, temp config to delete, why not) for oxlint with the Foldkit plugin over the programs."""
    pkg_abs = os.path.join(root, pkg)
    exe = os.path.join(pkg_abs, "node_modules", ".bin", "oxlint")
    if not os.path.isfile(exe):
        exe = shutil.which("oxlint") or ""
    if not exe:
        return None, None, "oxlint is not installed (no node_modules/.bin/oxlint, none on PATH)"
    for name in (".oxlintrc.json", "oxlintrc.json", ".oxlintrc.jsonc"):
        if os.path.isfile(os.path.join(pkg_abs, name)):
            return [exe, "-c", name, "--format", "json", *targets], None, None
    rec = os.path.join(pkg_abs, "node_modules", "@foldkit", "oxlint-plugin", "recommended.json")
    if not os.path.isfile(rec):
        return None, None, "no .oxlintrc.json and @foldkit/oxlint-plugin is not installed"
    fd, tmp = tempfile.mkstemp(prefix="sdlc-oxlint-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"extends": [rec]}, fh)
    return [exe, "-c", tmp, "--format", "json", *targets], tmp, None


def run_oxlint(root: str, fe: dict) -> tuple[list[Finding], str | None, str]:
    """(findings, note, skip key prefix). A note with no findings means oxlint did not run; the ratchet
    then treats `lint|<programs>/...` keys as not checked (never stale, kept by --prune)."""
    bases = program_bases(fe) or [""]
    pkg = package_dir(root, bases)
    skip = "lint|" + (bases[0] + "/" if bases[0] else "")
    prefix = pkg + "/" if pkg else ""
    targets = [b[len(prefix):] if b.startswith(prefix) else b for b in bases]
    targets = [t or "." for t in targets]
    argv, tmp, why = oxlint_command(root, pkg, targets)
    if argv is None:
        return [], f"oxlint: {why}; Foldkit lint rules not ratcheted", skip
    try:
        proc = subprocess.run(argv, cwd=os.path.join(root, pkg), capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"oxlint: did not run ({exc}); Foldkit lint rules not ratcheted", skip
    finally:
        if tmp:
            os.remove(tmp)
    if proc.returncode not in (0, 1):
        return [], f"oxlint: exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-160:]}; Foldkit lint rules not ratcheted", skip
    findings, err = parse_oxlint(proc.stdout, root, pkg)
    if err:
        return [], f"oxlint: {err}; Foldkit lint rules not ratcheted", skip
    return findings, None, skip


# ---------- detection and summary ----------

def _deps(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(data.get(key), dict):
            out.update(data[key])
    out["__workspaces__"] = data.get("workspaces")
    return out


def _workspace_dirs(root: str, declared) -> list[str]:
    pats: list[str] = []
    if isinstance(declared, dict):
        declared = declared.get("packages")
    if isinstance(declared, list):
        pats += [p for p in declared if isinstance(p, str)]
    ws = os.path.join(root, "pnpm-workspace.yaml")
    if os.path.isfile(ws):
        with open(ws, encoding="utf-8", errors="replace") as fh:
            in_pkgs = False
            for line in fh:
                if re.match(r"^packages\s*:", line):
                    in_pkgs = True
                    continue
                if in_pkgs:
                    m = re.match(r"^\s+-\s*['\"]?([^'\"#\s]+)", line)
                    if m:
                        pats.append(m.group(1))
                    elif line.strip() and not line.startswith((" ", "\t")):
                        in_pkgs = False
    dirs = []
    for pat in pats:
        if pat.startswith("!"):
            continue
        for d in sorted(glob.glob(os.path.join(root, pat))):
            if os.path.isfile(os.path.join(d, "package.json")):
                dirs.append(os.path.relpath(d, root).replace(os.sep, "/"))
    if not dirs:  # no workspace declared: package.json one or two levels down (web/, apps/console/)
        for d in sorted(glob.glob(os.path.join(root, "*", "package.json")) + glob.glob(os.path.join(root, "*", "*", "package.json"))):
            rel = os.path.relpath(os.path.dirname(d), root).replace(os.sep, "/")
            if not any(p in SKIP_PARTS or p.startswith(".") for p in rel.split("/")):
                dirs.append(rel)
    return dirs


#: Directory names whose Foldkit views handle credentials or money; an edit there needs a tier-high contract (DESIGN 11.3).
SENSITIVE_VIEW_DIRS = ("auth", "login", "signin", "signup", "payment*", "billing", "checkout")


def tier_high_globs(frontend: dict | None) -> list[str]:
    """The auth and payment view globs to add to ``tier_high_paths`` for a detected frontend block: each sensitive
    directory anywhere below the ``src`` of the package that holds the programs (``apps/console/src/programs/**`` gives
    ``apps/console/src/auth/**`` and ``apps/console/src/**/auth/**``)."""
    programs = (frontend or {}).get("programs") or ["src/programs/**"]
    head = str(programs[0]).split("src/", 1)[0] if "src/" in str(programs[0]) else ""
    return [f"{head}src/{mid}{d}/**" for d in SENSITIVE_VIEW_DIRS for mid in ("", "**/")]   # `**/` needs one directory


def detect(root: str) -> dict | None:
    """The frontend block for .claude/sdlc.json when the root package.json or a workspace package
    depends on foldkit (dependencies, devDependencies or peerDependencies); paths are repo-relative."""
    root_deps = _deps(os.path.join(root, "package.json"))
    for d in [""] + _workspace_dirs(root, root_deps.get("__workspaces__")):
        deps = root_deps if d == "" else _deps(os.path.join(root, d, "package.json"))
        if FRAMEWORK in deps:
            prefix = d + "/" if d else ""
            return {"framework": FRAMEWORK, "programs": [prefix + "src/programs/**"],
                    "api_table": prefix + "src/lib/api/endpoints.ts", "oxlint": True}
    return None


def summary(root: str, cfg: dict, oxlint: bool = True) -> dict | None:
    """The verify stamp's frontend block, with the findings per checker for the gate."""
    fe = frontend_of(cfg)
    if fe is None:
        return None
    tree = Tree(root, fe)
    s_find, stories = check_story(tree)
    c_find, scenes = check_scene(tree)
    a_find, api = check_api(tree)
    ox: dict = {"findings": None}
    if oxlint and fe.get("oxlint"):
        found, note, _ = run_oxlint(root, fe)
        ox = {"findings": None if note else len(found)}
        if note:
            ox["note"] = note
    return {"framework": FRAMEWORK, "programs": len({program_of(r, tree.bases) for r in tree.sources} - set(tree.bases)),
            "stories": stories, "scenes": scenes, "api": api, "oxlint": ox,
            "_findings": s_find + c_find + a_find}


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    root = cl.project_root(argv[argv.index("--root") + 1] if "--root" in argv and argv.index("--root") + 1 < len(argv) else None)
    if "--detect" in argv:
        print(json.dumps(detect(root)))
        return 0
    cfg = cl.load_config(root)
    if "--summary" in argv:
        out = summary(root, cfg)
        if out:
            out.pop("_findings", None)
        print(json.dumps(out))
        return 0
    if "--check" not in argv or argv.index("--check") + 1 >= len(argv) or argv[argv.index("--check") + 1] not in CHECKS:
        print(f"usage: frontend_foldkit.py --check {{{','.join(CHECKS)}}} --root DIR | --summary | --detect", file=sys.stderr)
        return 2
    fe = frontend_of(cfg)
    if fe is None:
        return 0
    findings, _ = CHECKS[argv[argv.index("--check") + 1]](Tree(root, fe))
    for key, msg in findings:
        print(f"{key}\t{msg}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
