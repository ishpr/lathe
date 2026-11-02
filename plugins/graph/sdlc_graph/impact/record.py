"""Predicted versus actual: store what ``affected`` predicted for a contract, then the tests that
actually failed, and score the prediction (precision and recall).

Matching: an actual failing test id matches a predicted entry when they are equal, or when the
prediction is a bare file path equal to the part of the actual id before ``::``.

``outcome`` also takes a JUnit XML report (``pytest --junitxml=...``) in place of a list: the
failing ids are the ``testcase`` elements with a ``failure`` or ``error`` child.

Predictions are PROPOSALS: nothing here changes ledger state.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from sdlc_graph.impact import affected as affected_mod
from sdlc_graph.shapes import AffectedSet


def _matches(prediction: str, actual: str) -> bool:
    return prediction == actual or ("::" not in prediction and actual.split("::", 1)[0] == prediction)


def score(predicted: list[str], actual: list[str]) -> tuple[float | None, float | None]:
    """``(precision, recall)``. Pure.

    precision = matched predictions / predictions (None when nothing predicted);
    recall = matched actual failures / actual failures (None when nothing failed).
    A bare-path prediction counts as matched when any actual id falls in that file.
    """
    pred = sorted(set(predicted))
    act = sorted(set(actual))
    precision = sum(any(_matches(p, a) for a in act) for p in pred) / len(pred) if pred else None
    recall = sum(any(_matches(p, a) for p in pred) for a in act) / len(act) if act else None
    return precision, recall


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def predict(conn: psycopg.Connection, snapshot_id: int, result: AffectedSet | None = None, *,
            slug: str | None = None, inputs: dict[str, Any] | None = None,
            paths: list[str] | None = None, symbols: list[str] | None = None,
            depth: int = 4) -> dict[str, Any]:
    """Insert a prediction row from an AffectedSet (tests -> predicted_tests, routes ->
    predicted_routes). Does not touch ledger state.

    ``result`` may be omitted: the affected set is then computed here, from ``paths``/``symbols``
    when given, else from the contract ``slug`` (its touched_symbols and capabilities.paths).

    Returns:
        ``{"prediction_id", "node": slug|None, "snapshot_id", "predicted_tests", "predicted_routes"}``.

    Raises:
        LookupError: unknown slug or snapshot. ValueError: nothing to predict from.
    """
    node_id = None
    if slug is not None:
        row = conn.execute("SELECT id FROM node WHERE slug = %s", (slug,)).fetchone()
        if row is None:
            raise LookupError(f"unknown node {slug!r}")
        node_id = row["id"]
    if conn.execute("SELECT 1 FROM snapshot WHERE id = %s", (snapshot_id,)).fetchone() is None:
        raise LookupError(f"unknown snapshot {snapshot_id}")
    if result is None:
        if paths or symbols:
            result = affected_mod.affected(conn, snapshot_id, paths=paths, symbols=symbols, depth=depth)
            inputs = {"paths": paths or [], "symbols": symbols or [], "depth": depth, **(inputs or {})}
        elif slug is not None:
            result = affected_mod.affected_for_node(conn, slug, snapshot_id)
        else:
            raise ValueError("predict needs a result, paths/symbols, or a node slug")
    tests = list(result.get("order") or result.get("tests") or [])
    routes = sorted(result.get("routes") or [])
    row = conn.execute(
        """INSERT INTO prediction (node_id, snapshot_id, predicted_tests, predicted_routes, inputs)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (node_id, snapshot_id, tests, routes, Jsonb(inputs or {})),
    ).fetchone()
    return {"prediction_id": int(row["id"]), "node": slug, "snapshot_id": snapshot_id,
            "predicted_tests": tests, "predicted_routes": routes}


def failing_from_junit(path: str | os.PathLike[str], known_files: set[str] | None = None) -> list[str]:
    """Pytest node ids of the failed or errored testcases in a JUnit XML report.

    The ``file`` attribute is used when pytest wrote it. Otherwise ``classname`` (dotted module,
    optionally followed by test classes) is mapped to a file: the longest dotted prefix that is a
    known test file wins, else the prefix before the first ``Test*`` part.

    Raises:
        FileNotFoundError / ValueError: unreadable or not JUnit XML.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path}: not valid JUnit XML ({exc})") from exc
    known = known_files or set()
    failing: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        name = case.get("name", "")
        file = case.get("file")
        parts = [p for p in (case.get("classname") or "").split(".") if p]
        if file:
            owners_ = parts[len(file[:-3].split("/")):] if file.endswith(".py") else []
            segs = file.split("/")
            file = next(("/".join(segs[i:]) for i in range(len(segs)) if "/".join(segs[i:]) in known), file)
            failing.append("::".join([file, *owners_, name]))
            continue
        chosen = None
        # pytest prefixes classname with the path from ITS rootdir, which is the repo root only
        # when the repo has a pytest config; a checkout nested under another project's config
        # (the demo's .demo/sample-app) gives ".demo.sample-app.tests...". Leading parts are therefore
        # dropped until a known test file matches, longest file first.
        for start in range(len(parts)):
            for k in range(len(parts), start, -1):
                cand = "/".join(parts[start:k]) + ".py"
                if cand in known:
                    chosen = (cand, parts[k:])
                    break
            if chosen:
                break
        if chosen is None:
            k = next((i for i, p in enumerate(parts) if p[:1].isupper()), len(parts))
            chosen = ("/".join(parts[:k]) + ".py", parts[k:])
        failing.append("::".join([chosen[0], *chosen[1], name]))
    return sorted(set(failing))


def record_outcome(conn: psycopg.Connection, prediction_id: int,
                   actual_failing_tests: list[str] | str | os.PathLike[str]) -> dict[str, Any]:
    """Upsert the outcome and its scores.

    ``actual_failing_tests`` is a list of pytest node ids, or the path of a JUnit XML report.

    Returns:
        ``{"prediction_id", "actual_failing_tests", "precision", "recall", "matched": [...],
        "missed": [actual ids not predicted], "extra": [predictions that did not fail]}``.

    Raises:
        LookupError: unknown prediction. ValueError: unreadable JUnit report.
    """
    row = conn.execute("SELECT predicted_tests FROM prediction WHERE id = %s", (prediction_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown prediction {prediction_id}")
    predicted = list(row["predicted_tests"])
    if isinstance(actual_failing_tests, (str, os.PathLike)):
        known = {r["path"] for r in conn.execute(
            "SELECT DISTINCT s.path FROM sym s JOIN prediction p ON p.snapshot_id = s.snapshot_id "
            "WHERE p.id = %s AND s.kind IN ('test','module')", (prediction_id,)).fetchall()}
        actual = failing_from_junit(Path(actual_failing_tests), known)
    else:
        actual = sorted(set(actual_failing_tests))
    precision, recall = score(predicted, actual)
    conn.execute(
        """INSERT INTO prediction_outcome (prediction_id, actual_failing_tests, precision, recall, recorded_at)
           VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (prediction_id) DO UPDATE
             SET actual_failing_tests = EXCLUDED.actual_failing_tests, precision = EXCLUDED.precision,
                 recall = EXCLUDED.recall, recorded_at = now()""",
        (prediction_id, actual, precision, recall),
    )
    return {
        "prediction_id": prediction_id,
        "actual_failing_tests": actual,
        "precision": precision,
        "recall": recall,
        "matched": [a for a in actual if any(_matches(p, a) for p in predicted)],
        "missed": [a for a in actual if not any(_matches(p, a) for p in predicted)],
        "extra": [p for p in predicted if not any(_matches(p, a) for a in actual)],
    }


#: Short name used by the CLI and the demo.
outcome = record_outcome


def summary(conn: psycopg.Connection, repo: str | None = None) -> dict[str, Any]:
    """Mean precision and recall over the recorded outcomes of a repo's predictions.

    Returns:
        ``{"repo", "predictions", "outcomes", "scored_precision", "scored_recall",
        "mean_precision", "mean_recall"}``. Means skip outcomes whose score is undefined (nothing
        predicted, or nothing failed) and are ``None`` when no outcome is scored; the ``scored_*``
        counts say how many outcomes each mean is over.
    """
    where, params = ("WHERE s.repo = %s", (repo,)) if repo else ("", ())
    row = conn.execute(
        f"""SELECT count(DISTINCT p.id) AS predictions, count(o.prediction_id) AS outcomes,
                   count(o.precision) AS scored_precision, count(o.recall) AS scored_recall,
                   avg(o.precision) AS mean_precision, avg(o.recall) AS mean_recall
              FROM prediction p JOIN snapshot s ON s.id = p.snapshot_id
              LEFT JOIN prediction_outcome o ON o.prediction_id = p.id {where}""",
        params,
    ).fetchone()
    return {
        "repo": repo,
        "predictions": int(row["predictions"]),
        "outcomes": int(row["outcomes"]),
        "scored_precision": int(row["scored_precision"]),
        "scored_recall": int(row["scored_recall"]),
        "mean_precision": _num(row["mean_precision"]),
        "mean_recall": _num(row["mean_recall"]),
    }
