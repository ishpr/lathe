"""A fake ``claude -p --output-format json`` for the judge and the conformance reviewer in tests.

``ORCHESTRATOR_CLAUDE="<python> -m sdlc_graph.orchestrator.fake_claude"``. It answers the schema it
is given: a conformance schema (``score``) gets the next score from ``FAKE_CLAUDE_SCORES`` (comma
separated, consumed in order through the counter file ``FAKE_CLAUDE_STATE``; default 0.95); a judge
schema (``choice``) gets ``FAKE_JUDGE_CHOICE`` (default the first listed option) and
``FAKE_JUDGE_ESCALATE=1`` to escalate; an assumption-review schema (``inconsistent``) gets
``FAKE_ASSUMPTION_INCONSISTENT`` (a JSON list of ``{"assumption": n, "detail": "..."}``; default none). Every call is appended to ``FAKE_CLAUDE_LOG`` when set.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _next_score() -> float:
    scores = [float(s) for s in (os.environ.get("FAKE_CLAUDE_SCORES") or "0.95").split(",") if s.strip()]
    state = os.environ.get("FAKE_CLAUDE_STATE")
    i = 0
    if state:
        p = Path(state)
        i = int(p.read_text() or 0) if p.exists() else 0
        p.write_text(str(i + 1))
    return scores[min(i, len(scores) - 1)]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    schema = json.loads(argv[argv.index("--json-schema") + 1]) if "--json-schema" in argv else {}
    prompt = argv[-1] if argv else ""
    if os.environ.get("FAKE_CLAUDE_SLEEP"):
        import time

        time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
    props = schema.get("properties") or {}
    if "score" in props:
        score = _next_score()
        data = {"score": score, "findings": [{"criterion": "AC1", "verdict": "met" if score >= 0.8 else "partial",
                                              "detail": f"fake reviewer score {score}"}]}
    elif "inconsistent" in props:
        data = {"inconsistent": json.loads(os.environ.get("FAKE_ASSUMPTION_INCONSISTENT") or "[]")}
    elif "choice" in props:
        m = re.search(r"^Options: (.+)$", prompt, re.M)
        options = [o.strip() for o in m.group(1).split(",")] if m else ["retry"]
        data = {"choice": os.environ.get("FAKE_JUDGE_CHOICE") or options[0], "reason": "fake judge",
                "escalate": os.environ.get("FAKE_JUDGE_ESCALATE") == "1"}
    else:
        data = {}
    if os.environ.get("FAKE_CLAUDE_LOG"):
        with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
            fh.write(json.dumps({"argv": argv[:-1], "answer": data}) + "\n")
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "total_cost_usd": 0.0,
                      "result": json.dumps(data), "structured_output": data, "usage": {"input_tokens": 10, "output_tokens": 5}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
