"""Bounded model calls: the judge (Gas Town's Mayor reduced to a function with a schema) and the
conformance reviewer. Both are one ``claude -p --output-format json --max-turns 6`` session with a
strict ``--json-schema``; the loop never lets a model decide anything else.

``ORCHESTRATOR_CLAUDE`` substitutes the binary (tests use ``fake_claude.py``). The JSON result's
``structured_output`` is read when present, else the ``result`` text is parsed as JSON.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import psycopg

from sdlc_graph.ledger import evidence, nodes, runs

from . import CONFORMANCE_KIND, autonomy, gitops, store
from .config import Config, claude_command

MAX_TURNS = 6

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["choice", "reason", "escalate"],
    "properties": {"choice": {"type": "string"}, "reason": {"type": "string"}, "escalate": {"type": "boolean"}},
}
CONFORMANCE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["score", "findings"],
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["criterion", "verdict", "detail"],
            "properties": {"criterion": {"type": "string"}, "verdict": {"type": "string", "enum": ["met", "partial", "missing", "violated"]},
                           "detail": {"type": "string"}}}},
    },
}


ASSUMPTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["inconsistent"],
    "properties": {"inconsistent": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["assumption", "detail"],
        "properties": {"assumption": {"type": "integer"}, "detail": {"type": "string"}}}}},
}


class ModelCallError(RuntimeError):
    pass


def parse_output(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(structured, envelope)`` from ``claude -p --output-format json`` stdout."""
    try:
        env = json.loads(stdout)
    except ValueError as exc:
        raise ModelCallError(f"claude did not print JSON: {stdout[:300]!r}") from exc
    if isinstance(env, list):  # stream-ish array: take the result event
        env = next((e for e in reversed(env) if isinstance(e, dict) and e.get("type") == "result"), {})
    if not isinstance(env, dict):
        raise ModelCallError("unexpected claude output shape")
    if env.get("is_error"):
        raise ModelCallError(f"claude reported an error: {str(env.get('result'))[:300]}")
    data = env.get("structured_output")
    if not isinstance(data, dict):
        text = env.get("result") if isinstance(env.get("result"), str) else ""
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ModelCallError(f"no JSON object in the result: {text[:300]!r}")
        try:
            data = json.loads(m.group(0))
        except ValueError as exc:
            raise ModelCallError(f"result is not valid JSON: {text[:300]!r}") from exc
    return data, env


def call(prompt: str, schema: dict[str, Any], *, model: str, cwd: str | Path | None = None,
         timeout: float = 600, tools: str = "Read Grep Glob") -> tuple[dict[str, Any], dict[str, Any]]:
    argv = [*claude_command(), "-p", "--output-format", "json", "--max-turns", str(MAX_TURNS), "--model", model,
            "--json-schema", json.dumps(schema), "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--allowedTools", tools,
            "--no-session-persistence", prompt]
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ModelCallError(f"could not run {argv[0]}: {exc}") from exc
    if proc.returncode != 0 and not proc.stdout.strip():
        raise ModelCallError(f"{argv[0]} exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return parse_output(proc.stdout)


def _usage(env: dict[str, Any]) -> dict[str, Any]:
    u = env.get("usage") or {}
    return {"turns": env.get("num_turns"), "cost_usd": env.get("total_cost_usd"),
            "tokens_in": u.get("input_tokens"), "tokens_out": u.get("output_tokens")}


def _record(conn: psycopg.Connection, slug: str, step: str, stage: str, model: str, outcome: str,
            env: dict[str, Any] | None, summary: str) -> int:
    run = store.start_run(conn, slug, step=step, stage=stage, model=model, attempt=1)
    store.finish_run(conn, run["id"], outcome, usage=_usage(env or {}), summary=summary)
    return int(run["id"])


# --------------------------------------------------------------------------- conformance


def conformance(conn: psycopg.Connection, cfg: Config, slug: str, wt: str | Path, start_commit: str,
                *, attempt: int) -> dict[str, Any]:
    """Score the diff against the contract's acceptance criteria and store ``conformance``
    evidence. Returns ``{"score", "findings", "passed", "evidence_id", "threshold"}``.

    Raises:
        ModelCallError: the reviewer call failed or returned something off-schema.
    """
    node = nodes.get_row(conn, slug)
    acceptance = node.get("acceptance") or []
    diff = gitops.diff_text(wt, start_commit)
    decided = [d for d in autonomy.resolved_decisions(conn, slug) if not d.get("severity")]
    latest = autonomy.latest_assumptions(conn, slug)
    assumed = ((latest or {}).get("payload") or {}).get("assumptions") or []
    prompt = "\n".join([
        "You are a conformance reviewer. Compare the diff below with the contract's acceptance criteria and",
        "say whether the change builds what the contract asks for, nothing more and nothing less. Tests passing",
        "is not your question; whether each criterion is implemented is. Read files in the working directory if",
        "the diff is not enough. Output only the JSON the schema asks for: score in [0,1] (the share of criteria",
        "met, minus a penalty for scope beyond the contract) and one finding per criterion.",
        "", f"Contract `{slug}`: {node['title']}", "Acceptance criteria:",
        *[f"- {r.get('id')}: {r.get('text')}" + (f" (test {r['test']})" if r.get("test") else "") for r in acceptance],
        "", f"Allowed paths: {', '.join((node.get('capabilities') or {}).get('paths') or [])}",
        "", "Decisions made on the contract (an implementation or assumption that goes against one is 'violated'):",
        *([f"- #{d['id']} {d['question']} -> {d['choice']}" for d in decided] or ["- none"]),
        "", "Defaults the session applied instead of asking (its assumptions; judge whether each is consistent",
        "with the decisions and the criteria):",
        *([f"- {a.get('default_applied')} (where: {a.get('where')}; why: {a.get('why')})" for a in assumed] or ["- none"]),
        "", "Diff (everything since the implement step started):", "```diff", diff, "```",
    ])
    model = runs.model_for_tier(runs.tier_for_stage("review.lens"))
    try:
        data, env = call(prompt, CONFORMANCE_SCHEMA, model=model, cwd=wt)
    except ModelCallError as exc:
        _record(conn, slug, "conformance", "review.lens", model, "error", None, f"conformance reviewer failed: {exc}")
        raise
    score = data.get("score")
    findings = data.get("findings")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= float(score) <= 1 or not isinstance(findings, list):
        _record(conn, slug, "conformance", "review.lens", model, "error", env, f"off-schema conformance output: {data!r}"[:500])
        raise ModelCallError(f"conformance output off schema: {data!r}"[:300])
    passed = float(score) >= cfg.conformance_threshold
    run_id = _record(conn, slug, "conformance", "review.lens", model, "ok" if passed else "below_threshold", env,
                     f"conformance {float(score):.2f} (threshold {cfg.conformance_threshold}); {len(findings)} findings")
    ev = evidence.add(conn, slug, CONFORMANCE_KIND,
                      {"score": float(score), "findings": findings, "threshold": cfg.conformance_threshold,
                       "passed": passed, "attempt": attempt, "run_id": run_id, "start_commit": start_commit,
                       "head": gitops.head(wt)})
    return {"score": float(score), "findings": findings, "passed": passed, "evidence_id": ev["id"],
            "threshold": cfg.conformance_threshold}


def assumptions_review(conn: psycopg.Connection, cfg: Config, slug: str, wt: str | Path, assumptions: list[dict[str, Any]],
                       *, step: str) -> list[str]:
    """One bounded call after any step: does each assumption the session recorded agree with the contract's decisions
    (the ledger's and the contract.md Decisions table)? Returns the issues (empty: all consistent)."""
    decided = [d for d in autonomy.resolved_decisions(conn, slug) if not d.get("severity")] + autonomy.contract_decisions(wt, slug)
    if not decided or not assumptions:
        return []
    prompt = "\n".join([
        "You review the defaults an unattended session applied instead of asking. A default may never contradict a",
        "decision. List every assumption, by its number, that contradicts one of the decisions below (it applies an",
        "option the decision did not choose, or behaves the other way when a decision settles that behaviour).",
        "Output only the JSON the schema asks for; an empty list means every assumption is consistent.",
        "", "Decisions:",
        *[f"- {'#' if isinstance(d['id'], int) else ''}{d['id']} {d['question']} (options: "
          f"{', '.join(v for o in d.get('options') or [] for v in autonomy._values(o))}) -> {d['choice']}" for d in decided],
        "", f"Assumptions recorded during the {step} step:",
        *[f"{i}. {a.get('default_applied')} (where: {a.get('where')}; why: {a.get('why')}; decision: {a.get('decision') or '?'})"
          for i, a in enumerate(assumptions, 1)],
    ])
    model = runs.model_for_tier(runs.tier_for_stage("review.lens"))
    try:
        data, env = call(prompt, ASSUMPTION_SCHEMA, model=model, cwd=wt)
    except ModelCallError as exc:
        _record(conn, slug, "assumptions", "review.lens", model, "error", None, f"assumption reviewer failed: {exc}")
        raise
    bad = data.get("inconsistent")
    if not isinstance(bad, list):
        _record(conn, slug, "assumptions", "review.lens", model, "error", env, f"off-schema assumption review: {data!r}"[:500])
        raise ModelCallError(f"assumption review off schema: {data!r}"[:300])
    _record(conn, slug, "assumptions", "review.lens", model, "ok" if not bad else "inconsistent", env,
            f"{step}: {len(assumptions)} assumption(s) reviewed; {len(bad)} inconsistent")
    return [f"assumption {b.get('assumption')} contradicts a decision: {b.get('detail')}" for b in bad if isinstance(b, dict)]


# --------------------------------------------------------------------------- judge


def judge(conn: psycopg.Connection, slug: str, *, question: str, options: list[str], context: dict[str, Any]) -> dict[str, Any]:
    """One bounded Opus call: pick one of ``options``, explain, or escalate.

    Returns ``{"choice", "reason", "escalate"}``; an off-list choice becomes an escalation.
    Raises ModelCallError when the call fails.
    """
    model = runs.model_for_tier("orchestrator")
    prompt = "\n".join([
        "You are the orchestrator's judge. Decide one question about a contract in an automated pipeline, using",
        "only the ledger state below. Pick exactly one option, give the reason in one or two sentences, and set",
        "escalate to true when a human should decide instead (ambiguity, risk, repeated failure with no new",
        "information). Output only the JSON the schema asks for.",
        "", f"Question: {question}", f"Options: {', '.join(options)}",
        "", "Ledger state:", json.dumps(context, indent=1, default=str)[:20_000],
    ])
    try:
        data, env = call(prompt, JUDGE_SCHEMA, model=model, tools="Read")
    except ModelCallError as exc:
        _record(conn, slug, "judge", "review.synthesis", model, "error", None, f"judge failed: {exc}")
        raise
    choice, reason, esc = data.get("choice"), str(data.get("reason") or ""), bool(data.get("escalate"))
    if choice not in options:
        esc, reason = True, f"judge chose {choice!r}, not one of {options}; escalating. {reason}"
    _record(conn, slug, "judge", "review.synthesis", model, "escalate" if esc else "ok", env,
            f"judge: {choice} ({'escalate' if esc else 'proceed'}): {reason}"[:1000])
    return {"choice": choice, "reason": reason, "escalate": esc}
