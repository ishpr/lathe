#!/usr/bin/env python3
"""Live route sweep: does every route in the OpenAPI spec refuse an unauthenticated call?

Why: the static route-auth checker reads source; this one asks the running app. REC-09
(routes answered without a key because auth was opt-in per route). It fetches the spec from a running server, sends
one request per path and method with no credentials (path parameters filled with '1', no body,
redirects not followed) and expects the status to be in --expect (default 401,403). A route listed
in the public-routes file (lines "METHOD PATH reason", the same file the route-auth checker reads)
is exempt: it is shown as PUBLIC and never fails. Any other route answering outside the expected
set fails the run, and so does a route that cannot be reached.

Usage: route_sweep.py --base-url http://localhost:8000 [--openapi-path /openapi.json]
           [--public-file .claude/public_routes.txt] [--expect 401,403] [--root DIR] [--timeout 10]

Standard library only. Point it at a throwaway instance: it issues POST/PUT/PATCH/DELETE requests
without credentials, which a correct app rejects, and a broken one may execute.
Exit 0 every route behaved, 1 at least one route answered outside the expected set, 2 misuse or
the spec could not be fetched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

METHODS = ("get", "post", "put", "patch", "delete")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401 - urllib hook
        return None


def _open(req: urllib.request.Request, timeout: float) -> int:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def fetch_spec(base: str, path: str, timeout: float) -> dict:
    req = urllib.request.Request(base + path, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_public(path: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[0].upper() in {m.upper() for m in METHODS} | {"*", "ANY"}:
                    out.add((parts[0].upper(), normalise(parts[1])))
    except OSError:
        pass
    return out


def normalise(path: str) -> str:
    """Compare templates, so /runs/{id} and /runs/{run_id} are the same route."""
    return re.sub(r"\{[^}]*\}", "{}", path).rstrip("/") or "/"


def routes_from_spec(spec: dict) -> list[tuple[str, str]]:
    out = []
    for path, item in (spec.get("paths") or {}).items():
        for m in METHODS:
            if isinstance(item, dict) and m in item:
                out.append((m.upper(), path))
    return sorted(out, key=lambda r: (r[1], r[0]))


def fill(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "1", path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Unauthenticated sweep of an OpenAPI spec against a running server.")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--openapi-path", default=None, help="default: route_sweep.openapi_path in .claude/sdlc.json, else /openapi.json")
    ap.add_argument("--public-file", default=None, help="default: .claude/public_routes.txt under --root")
    ap.add_argument("--expect", default=None, help="comma-separated status codes, default 401,403")
    ap.add_argument("--root", default=".")
    ap.add_argument("--timeout", type=float, default=10.0)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2

    cfg: dict = {}
    try:
        with open(os.path.join(args.root, ".claude", "sdlc.json"), encoding="utf-8") as fh:
            cfg = (json.load(fh).get("route_sweep") or {})
    except (OSError, ValueError):
        pass
    openapi_path = args.openapi_path or cfg.get("openapi_path") or "/openapi.json"
    try:
        expect = {int(x) for x in (args.expect.split(",") if args.expect else cfg.get("expect_unauthenticated") or [401, 403])}
    except ValueError:
        sys.stderr.write("route_sweep: --expect must be a comma-separated list of status codes\n")
        return 2
    pub_path = args.public_file or os.path.join(".claude", "public_routes.txt")
    if not os.path.isabs(pub_path):
        pub_path = os.path.join(args.root, pub_path)
    public = read_public(pub_path)
    base = args.base_url.rstrip("/")

    try:
        spec = fetch_spec(base, openapi_path, args.timeout)
    except Exception as exc:  # noqa: BLE001 - any failure to read the spec is a misuse/exit 2
        sys.stderr.write(f"route_sweep: cannot fetch {base}{openapi_path}: {exc}\n")
        return 2
    routes = routes_from_spec(spec)
    if not routes:
        sys.stderr.write("route_sweep: the spec lists no routes\n")
        return 2

    rows = []
    bad = 0
    for method, path in routes:
        is_public = (method, normalise(path)) in public or ("*", normalise(path)) in public or ("ANY", normalise(path)) in public
        req = urllib.request.Request(base + fill(path), method=method)
        if method in ("POST", "PUT", "PATCH"):
            req.add_header("Content-Type", "application/json")
            req.data = b"{}"
        try:
            status: int | str = _open(req, args.timeout)
        except Exception as exc:  # noqa: BLE001
            status = f"ERR {exc.__class__.__name__}"
        if is_public:
            verdict = "PUBLIC"
        elif isinstance(status, int) and status in expect:
            verdict = "ok"
        else:
            verdict = "FAIL"
            bad += 1
        rows.append((method, path, status, verdict))

    w = max(len(r[1]) for r in rows)
    print(f"{'METHOD':<7} {'PATH':<{w}} {'STATUS':<10} RESULT")
    for method, path, status, verdict in rows:
        print(f"{method:<7} {path:<{w}} {str(status):<10} {verdict}")
    print(f"\n{len(rows)} route(s), expected {sorted(expect)} unauthenticated, {bad} outside the expected set")
    if bad:
        print("FAIL: add the route to the public-routes file with a reason, or put it behind the auth dependency")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
