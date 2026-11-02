#!/usr/bin/env python3
"""Baseline metrics for the pack: run it now, at week 6 and at week 12 and compare.

Why: REC-46. Developers in the METR study estimated a speedup while measured time rose,
so the pack needs its own numbers: PR size, hours to merge, hours to first review, reverts, the
deletions/additions ratio (an agent-built repository can delete almost nothing it adds) and how often the
guards block (.claude/work/blocks.jsonl, one JSON line per deny).

Sources: `git log` always; `gh pr list` when gh is installed and authenticated. If gh is missing or
fails, the PR metrics fall back to merge/squash commits found in git log ("Merge pull request #n",
"(#n)") and the review/merge times print as n/a; the script never fails because gh is unavailable.

Usage: metrics_baseline.py [--root DIR] [--since 90d] [--no-write] [--no-gh]
  --since takes Nd, Nw or Nm (days, weeks, 30-day months). Prints a markdown table and writes
  docs/metrics/<date>.json under --root (skipped with --no-write).
Exit 0 ok, 2 misuse or not a git repository.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys


def run(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def parse_since(s: str) -> int:
    m = re.fullmatch(r"(\d+)([dwm])", s.strip())
    if not m:
        raise ValueError(f"--since must look like 90d, 12w or 3m, got {s!r}")
    return int(m.group(1)) * {"d": 1, "w": 7, "m": 30}[m.group(2)]


def iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def git_numstat(root: str, since: str) -> tuple[list[dict], int, int]:
    """Commits in range: [{sha, subject, added, deleted}], plus total added/deleted."""
    rc, out = run(["git", "log", f"--since={since}", "--no-merges", "--numstat", "--format=@@%H\t%s"], root)
    commits: list[dict] = []
    cur = None
    for line in out.splitlines():
        if line.startswith("@@"):
            sha, _, subj = line[2:].partition("\t")
            cur = {"sha": sha, "subject": subj, "added": 0, "deleted": 0}
            commits.append(cur)
        elif line.strip() and cur is not None:
            a, d, *_ = line.split("\t")
            if a.isdigit() and d.isdigit():
                cur["added"] += int(a)
                cur["deleted"] += int(d)
    return commits, sum(c["added"] for c in commits), sum(c["deleted"] for c in commits)


def gh_prs(root: str, since_date: str) -> tuple[list[dict] | None, str]:
    rc, out = run(
        ["gh", "pr", "list", "--state", "merged", "--limit", "500", "--search", f"merged:>={since_date}",
         "--json", "number,additions,deletions,createdAt,mergedAt,reviews"], root)
    if rc != 0:
        return None, (out.strip() or f"gh exited {rc}")[:120]
    try:
        return json.loads(out), ""
    except ValueError:
        return None, "gh returned non-JSON"


def block_stats(root: str, cutoff: dt.datetime) -> dict:
    path = os.path.join(root, ".claude", "work", "blocks.jsonl")
    by_rule: dict[str, int] = {}
    total = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    if iso(rec["ts"]) < cutoff:
                        continue
                    total += 1
                    key = f"{rec.get('hook', '?')}:{rec.get('rule', '?')}"
                    by_rule[key] = by_rule.get(key, 0) + 1
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        return {"total": None, "by_rule": {}}
    return {"total": total, "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1]))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Baseline metrics from git log, gh and the guard block log.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--since", default="90d")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--no-gh", action="store_true")
    try:
        args = ap.parse_args(argv)
        days = parse_since(args.since)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    except ValueError as exc:
        sys.stderr.write(f"metrics_baseline: {exc}\n")
        return 2
    root = os.path.abspath(args.root)
    rc, _ = run(["git", "rev-parse", "--git-dir"], root)
    if rc != 0:
        sys.stderr.write(f"metrics_baseline: {root} is not a git repository\n")
        return 2

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    commits, added, deleted = git_numstat(root, f"{days}.days.ago")
    reverts = sum(1 for c in commits if re.match(r"(?i)revert\b", c["subject"]))

    prs, gh_note = (None, "skipped (--no-gh)") if args.no_gh else gh_prs(root, cutoff.strftime("%Y-%m-%d"))
    source = "gh"
    if prs is not None:
        pr_count = len(prs)
        lines = [p["additions"] + p["deletions"] for p in prs]
        merge_h = [(iso(p["mergedAt"]) - iso(p["createdAt"])).total_seconds() / 3600 for p in prs if p.get("mergedAt")]
        review_h = []
        for p in prs:
            times = [iso(r["submittedAt"]) for r in p.get("reviews") or [] if r.get("submittedAt")]
            if times:
                review_h.append((min(times) - iso(p["createdAt"])).total_seconds() / 3600)
        ok_review = True
    else:
        source = "git-log"
        prc = [c for c in commits if re.search(r"\(#\d+\)\s*$", c["subject"])]
        rc2, merges = run(["git", "log", f"--since={days}.days.ago", "--merges", "--format=%s"], root)
        merge_n = sum(1 for s in merges.splitlines() if re.match(r"Merge pull request #\d+", s))
        pr_count = len(prc) + merge_n
        lines = [c["added"] + c["deleted"] for c in prc] or [c["added"] + c["deleted"] for c in commits]
        merge_h, review_h = [], []
        ok_review = False

    blocks = block_stats(root, cutoff)
    ratio = round(deleted / added, 4) if added else None
    result = {
        "date": now.strftime("%Y-%m-%d"),
        "since": args.since,
        "source": source,
        "gh_note": gh_note,
        "commits": len(commits),
        "pr_count": pr_count,
        "median_lines_changed": median(lines),
        "median_hours_to_merge": median(merge_h) if ok_review else None,
        "median_hours_to_first_review": median(review_h) if ok_review else None,
        "revert_count": reverts,
        "lines_added": added,
        "lines_deleted": deleted,
        "deletions_over_additions": ratio,
        "guard_blocks": blocks,
    }

    def cell(v, unit=""):
        return "n/a" if v is None else f"{v}{unit}"

    print(f"# Baseline metrics ({result['date']}, last {args.since}, PR source: {source})\n")
    if gh_note:
        print(f"> gh: {gh_note}; PR figures come from git log\n")
    print("| Metric | Value |\n|---|---|")
    print(f"| Commits (non-merge) | {len(commits)} |")
    print(f"| PRs merged | {pr_count} |")
    print(f"| Median lines changed per PR | {cell(result['median_lines_changed'])} |")
    print(f"| Median hours to merge | {cell(result['median_hours_to_merge'])} |")
    print(f"| Median hours to first review | {cell(result['median_hours_to_first_review'])} |")
    print(f"| Reverts | {reverts} |")
    print(f"| Deletions / additions | {cell(ratio)} ({deleted} / {added}) |")
    print(f"| Guard blocks | {cell(blocks['total'])} |")
    for k, v in blocks["by_rule"].items():
        print(f"|   {k} | {v} |")

    if not args.no_write:
        out_dir = os.path.join(root, "docs", "metrics")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, result["date"] + ".json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
        print(f"\nwrote {os.path.relpath(out_path, root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
