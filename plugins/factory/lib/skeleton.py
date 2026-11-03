"""Is this file a skeleton: declarations whose every body only raises or throws, and nothing else?

/tests may create a skeleton of a module the new tests import, so the imports resolve and each test fails for its own
reason (the behaviour is missing). The commit guard lets a tests-only commit through without a verify stamp; a
skeleton rides in that commit when it is a new file and this check holds, because a file with no logic in it cannot
carry unverified behaviour. Anything with a return value, a branch, a call or a computed constant is app code and
waits for /verify like any other change.

Python is judged on its syntax tree. TypeScript and JavaScript are judged on their text with comments and strings
blanked: no statement keywords, every function body empty or one `throw new Error("not implemented")`, no calls but
`new Error(...)`, and an arrow outside a type declaration or a parameter annotation must open a `{` body. The check
prefers refusing an odd skeleton over accepting logic: a refusal tells the author what to take out.
"""

from __future__ import annotations

import ast
import re

PY_SUFFIXES = (".py",)
TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def is_skeleton(rel: str, text: str) -> tuple[bool, str]:
    """(True, "") when the file holds declarations only; else (False, why) naming the first offending line."""
    low = rel.lower()
    if low.endswith(PY_SUFFIXES):
        return _python(text)
    if low.endswith(TS_SUFFIXES):
        return _typescript(text)
    return False, "only Python, TypeScript and JavaScript files can be skeletons"


# ----------------------------------------------------------------------------------------------------------- python

def _python(text: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return False, f"line {exc.lineno}: does not parse ({exc.msg})"
    for node in tree.body:
        why = _py_module_item(node)
        if why:
            return False, why
    return True, ""


def _py_module_item(node: ast.AST) -> str:
    if _is_docstring(node) or isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
        return ""
    if isinstance(node, ast.ClassDef):
        return _py_class(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _py_function(node)
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return _py_assignment(node)
    if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING" and not node.orelse \
            and all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in node.body):
        return ""
    return f"line {node.lineno}: a skeleton holds imports, classes, functions and constants only"


def _py_class(node: ast.ClassDef) -> str:
    for item in node.body:
        if _is_docstring(item) or isinstance(item, ast.Pass):
            continue
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            why = _py_function(item)
        elif isinstance(item, ast.ClassDef):
            why = _py_class(item)
        elif isinstance(item, (ast.Assign, ast.AnnAssign)):
            why = _py_assignment(item)
        else:
            why = f"line {item.lineno}: a class in a skeleton holds methods, attributes and nested classes only"
        if why:
            return why
    return ""


def _py_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = node.body[1:] if node.body and _is_docstring(node.body[0]) else node.body
    for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
        if not _py_constant(default):
            return f"line {default.lineno}: a default value in a skeleton is a constant"
    if len(body) == 1 and isinstance(body[0], ast.Raise) and _is_not_implemented(body[0].exc):
        return ""
    return f"line {node.lineno}: the body of {node.name} must be `raise NotImplementedError` and nothing else"


def _is_not_implemented(exc: ast.AST | None) -> bool:
    if isinstance(exc, ast.Name):
        return exc.id == "NotImplementedError"
    if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "NotImplementedError":
        return all(_py_constant(a) for a in exc.args) and all(_py_constant(k.value) for k in exc.keywords)
    return False


def _py_assignment(node: ast.Assign | ast.AnnAssign) -> str:
    value = node.value
    if value is None or _py_constant(value):
        return ""
    return f"line {node.lineno}: a value in a skeleton is a constant (a call or an expression is logic)"


def _py_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_py_constant(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _py_constant(k) for k in node.keys) and all(_py_constant(v) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _py_constant(node.operand)
    return isinstance(node, (ast.Name, ast.Attribute))  # a sentinel, an enum member, `None`-like names


def _is_docstring(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


# ------------------------------------------------------------------------------------------------- typescript / js

_FORBIDDEN = re.compile(r"\b(return|await|yield|if|else|for|while|do|switch|case|try|catch|finally|delete|void 0)\b")
_BODY_AFTER_RETURN_TYPE = re.compile(r"\)\s*:\s*[^;{}=()]*$")  # `): Promise<void> {` opens a body too
_THROW = re.compile(r"^\s*throw\s+new\s+Error\s*\(\s*(?:[\"'`]\s*[\"'`])?\s*\)\s*;?\s*$")  # the message is blanked, its quotes stay
_DECL_START = re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?(?:type|interface)\b")
_CALL_HEAD = re.compile(r"(?:^|[^\w$.])(?:function|constructor|new\s+Error|get|set|async)\s*$")


def _typescript(text: str) -> tuple[bool, str]:
    blanked, not_impl = _blank(text)
    if not_impl is False:
        return False, "every `throw new Error(...)` in a skeleton says \"not implemented\""
    hit = _FORBIDDEN.search(blanked)
    if hit:
        return False, f"line {_line(blanked, hit.start())}: `{hit.group(1)}` is logic; a skeleton declares and throws only"
    depth_paren = 0
    stack: list[tuple[str, int]] = []  # ("fn" | "other", start index) for each open `{`
    statement_start = 0
    in_type_decl = False
    i, n = 0, len(blanked)
    while i < n:
        ch = blanked[i]
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
            j = _skip_ws(blanked, i + 1)
            nxt = blanked[j:j + 2]
            # a `(...)` group is a declaration when `{`, `:`, `=>` or `;`-less type follows; otherwise it is a call
            if not (nxt[:1] in "{:" or nxt == "=>") and not _CALL_HEAD.search(blanked[:_open_paren(blanked, i)]):
                if not in_type_decl and depth_paren == 0:
                    return False, f"line {_line(blanked, i)}: a call in a skeleton (only `new Error(...)` is allowed)"
        elif ch == "{":
            prev = blanked[:i].rstrip()
            kind = "fn" if prev.endswith((")", "=>")) or _BODY_AFTER_RETURN_TYPE.search(prev) else "other"
            stack.append((kind, i))
        elif ch == "}":
            if stack:
                kind, start = stack.pop()
                if kind == "fn":
                    body = blanked[start + 1:i]
                    if body.strip() and not _THROW.match(body):
                        return False, (f"line {_line(blanked, start)}: a function body in a skeleton is empty or "
                                       "`throw new Error(\"not implemented\")` and nothing else")
            if not stack:
                in_type_decl = False
                statement_start = i + 1
        elif ch == "=" and blanked[i:i + 2] == "=>":
            j = _skip_ws(blanked, i + 2)
            if blanked[j:j + 1] != "{" and depth_paren == 0 and not in_type_decl:
                return False, f"line {_line(blanked, i)}: an arrow function in a skeleton opens a `{{ throw ... }}` body"
            i += 1
        elif ch == ";" and not stack:
            in_type_decl = False
            statement_start = i + 1
        if not stack and not in_type_decl and _DECL_START.match(blanked[statement_start:i + 1]):
            in_type_decl = True
        i += 1
    return True, ""


def _blank(text: str) -> tuple[str, bool | None]:
    """Comments and string contents replaced by spaces (newlines kept). The second value is False when a
    `throw new Error(...)` message does not say "not implemented", True when every one does, None with no throw."""
    out: list[str] = []
    i, n = 0, len(text)
    not_impl: bool | None = None
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif text[i] in "\"'`":
            q = text[i]
            j = i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            literal = text[i + 1:j - 1]
            before = "".join(out).rstrip()
            if re.search(r"throw\s+new\s+Error\s*\($", before):
                ok = "not implemented" in literal.lower() or "notimplemented" in literal.lower()
                not_impl = ok if not_impl in (None, True) else False
            out.append(q + re.sub(r"[^\n]", " ", literal) + q)
            i = j
        else:
            out.append(text[i])
            i += 1
    blanked = "".join(out)
    return blanked, not_impl


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _open_paren(s: str, close: int) -> int:
    depth = 0
    for k in range(close, -1, -1):
        if s[k] == ")":
            depth += 1
        elif s[k] == "(":
            depth -= 1
            if depth == 0:
                return k
    return 0


def _line(s: str, pos: int) -> int:
    return s.count("\n", 0, pos) + 1
