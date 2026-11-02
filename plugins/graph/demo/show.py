"""Format ``sdlc-graph ... --json`` output for the demo: one short, readable block per command.

Usage: sdlc-graph <cmd> --json | python demo/show.py <view> [args]
The views only read stdin and print; they never change anything. Unknown keys are ignored, so a
richer result never breaks the demo.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _short(items: list[str], n: int = 6) -> str:
    more = f" (+{len(items) - n} more)" if len(items) > n else ""
    return ", ".join(items[:n]) + more


def index(d: dict[str, Any]) -> None:
    print(f"  snapshot {d['snapshot_id']}  {d['repo']}@{d['commit_sha'][:10]}  storage={d['storage']}  "
          f"symbols={d['symbols']} edges={d['edges']} owner_rules={d['owners']}")


def delta(d: dict[str, Any]) -> None:
    print(f"  snapshot {d['snapshot_id']} (delta on {d['base_snapshot_id']})  {d['commit_sha'][:10]}  "
          f"symbols +{d['added']} -{d['removed']} ~{d['changed']}  edges +{d['edges_added']} -{d['edges_removed']}")


def verify(d: dict[str, Any]) -> None:
    diffs = {k: len(d[k]) for k in ("missing", "extra", "changed", "edges_missing", "edges_extra")}
    print(f"  verify snapshot {d['snapshot_id']}: ok={d['ok']}  diff={diffs}")


def impact(d: dict[str, Any]) -> None:
    roots = [s for s in d["symbols"] if s.get("depth") == 0]
    print(f"  roots: {_short([s['qualname'] for s in roots], 4)}")
    print(f"  reached {len(d['symbols'])} symbols (max depth {max((s.get('depth') or 0) for s in d['symbols'])})")
    print(f"  routes ({len(d['routes'])}): {', '.join(d['routes'])}")
    files: dict[str, int] = {}
    for t in d["tests"]:
        files[t.split("::")[0]] = files.get(t.split("::")[0], 0) + 1
    print(f"  tests ({len(d['tests'])}): " + ", ".join(f"{f} x{n}" for f, n in files.items()))
    print("  owners:")
    for path, handles in d["owners"].items():
        if not path.startswith("tests/"):
            print(f"    {path:<32} {' '.join(handles)}")
    test_owners = sorted({h for p, hs in d["owners"].items() if p.startswith("tests/") for h in hs})
    if test_owners:
        print(f"    {'tests/**':<32} {' '.join(test_owners)}")


def node(d: dict[str, Any]) -> None:
    print(f"  {d['kind']} {d['slug']!r} state={d['state']} tier={d.get('tier')}")


def link(d: dict[str, Any]) -> None:
    print(f"  {d['src']} -[{d['rel']}]-> {d['dst']}")


def draft(d: dict[str, Any]) -> None:
    print(f"  status={d['status']}")
    n = d.get("node")
    if n:
        acc = [a.get("id", "?") for a in n.get("acceptance") or []]
        print(f"  contract {n['slug']!r} state={n['state']} tier={n.get('tier') or '-'}")
        print(f"  touched: {', '.join(n.get('touched_symbols') or [])}")
        print("  acceptance: " + (f"{len(acc)} criteria ({', '.join(acc)})" if acc else "none yet"))
    for f in d.get("flags") or []:
        print(f"  intake flag, line {f['line']} ({', '.join(f.get('reasons') or [f['reason']])}): {f['text'][:70]}")
    for c in d.get("conflicts") or []:
        print(f"  CONFLICT with open contract {c['slug']!r} ({c['state']}) on {', '.join(c['overlap'])}")
    for c in d.get("duplicates") or []:
        print(f"  duplicate of {c}")


def decision(d: dict[str, Any]) -> None:
    if d.get("error"):
        print(f"  refused: {d['message']}")
        return
    state = f"resolved by {d['decided_by']}: {d['choice']}" if d.get("decided_by") else \
        f"open, proposed by {d['proposed_by']} (recommends {d.get('recommendation')})"
    print(f"  decision {d['id']}: {d['question']}  -> {state}")


def transition(d: dict[str, Any]) -> None:
    if d.get("ok"):
        cited = f"  citing evidence {d['evidence_ids']}" if d.get("evidence_ids") else ""
        print(f"  {d['from_state']} -> {d['to_state']}: OK{cited}")
    else:
        print(f"  {d.get('from_state')} -> {d.get('to_state')}: REFUSED, missing {d.get('missing')}")


def evidence(d: dict[str, Any]) -> None:
    print(f"  evidence {d['id']} {d['kind']:<20} source={d['source']:<45} binding={d['binding']}")


def workflow(d: dict[str, Any]) -> None:
    if d.get("done"):
        print(f"  workflow next [{d['state']}]: done (terminal state)")
        return
    who = "a human acts" if d.get("human_only") else "an agent may act"
    print(f"  workflow next [{d['state']} -> {d['next_state']}]: {d.get('command') or '(no command)'}  "
          f"stage={d.get('stage')} tier={d.get('tier')} model={d.get('model')}  ({who}; "
          f"missing {d.get('missing') or 'nothing'})")


def run(d: dict[str, Any]) -> None:
    print(f"  run {d['id']}: stage={d['stage']} tier={d['model_tier']} requested={d['requested_model']} "
          f"outcome={d.get('outcome')} tokens_in={d.get('tokens_in')} tokens_out={d.get('tokens_out')}")


def budget(d: dict[str, Any]) -> None:
    print(f"  Fable budget for epic {d['epic']!r}: used {d['used']} of {d['budget']}, remaining {d['remaining']}")


def predict(d: dict[str, Any]) -> None:
    print(f"  prediction {d['prediction_id']} for {d.get('node')} on snapshot {d['snapshot_id']}: "
          f"{len(d['predicted_tests'])} tests, routes {d['predicted_routes']}")
    print(f"  run first: {_short(d['predicted_tests'], 4)}")


def outcome(d: dict[str, Any], predicted: list[str] | None = None) -> None:
    p = d["precision"]
    r = d["recall"]
    print(f"  actually failing: {', '.join(d['actual_failing_tests']) or 'none'}")
    print(f"  precision={p if p is None else round(p, 3)}  recall={r if r is None else round(r, 3)}  "
          f"matched={len(d['matched'])} missed={len(d['missed'])} extra={len(d['extra'])}")


def caps(d: dict[str, Any]) -> None:
    s = d["settings"]
    print(f"  sandbox.enabled={s['sandbox']['enabled']}")
    print(f"  sandbox.filesystem.allowWrite: {', '.join(s['sandbox']['filesystem']['allowWrite'])}")
    print(f"  sandbox.network.allowedDomains: {', '.join(s['sandbox']['network']['allowedDomains'])}")
    print(f"  permissions.deny: {', '.join(s['permissions']['deny'])}")
    print(f"  permissions.allow: {len(s['permissions']['allow'])} rules ({_short(s['permissions']['allow'], 3)})")
    if d.get("unenforced"):
        print(f"  declared but NOT enforced by Claude Code: {json.dumps(d['unenforced'])}")


def spanner(d: dict[str, Any]) -> None:
    if not d.get("ok"):
        print(f"  spanner export failed: {d.get('error')}")
        return
    print(f"  {d['host']} {d['instance']}/{d['database']}: {d['statements']} DDL statements, loaded {d['rows_loaded']}")
    for name, q in d["queries"].items():
        print(f"  GQL {name}: {q['gql'][len('GRAPH SdlcGraph '):][:150]}...")
        for row in q["rows"][:8]:
            print("     " + " | ".join(str(v) for v in row))
        if len(q["rows"]) > 8:
            print(f"     ... {len(q['rows']) - 8} more rows")


VIEWS = {f.__name__: f for f in (index, delta, verify, impact, node, link, draft, decision, transition, evidence,
                                  workflow, run, budget, predict, outcome, caps, spanner)}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in VIEWS:
        print(f"usage: show.py {{{'|'.join(VIEWS)}}}", file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return 1
    if isinstance(data, dict) and data.get("error") and argv[1] != "decision":
        print(f"  error: {data.get('error')}: {data.get('message')}")
        return 1
    VIEWS[argv[1]](data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
