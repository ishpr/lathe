#!/usr/bin/env bash
# step_executor.sh: the live step executor for the sdlc-graph orchestrator.
#
# Usage: step_executor.sh --worktree WT --command /sdlc:<step> --prompt-file P --settings S
#                         --result-file R --log-file L --session-id UUID --model M --max-turns N
#                         --budget USD --reviewer @h --allowed-tools-file T
#                         --permission-mode dontAsk --permission-prompts none [--plugin-dir D]...
#
# Runs ONE `claude -p` session in WT, the way the sdlc pack's run_headless.sh does, minus the
# parts the orchestrator owns: it creates no worktree, checks no scope and pushes nothing (the
# orchestrator's gates and the /ship step do that). Flags passed to claude, as in run_headless.sh:
#   -p --permission-mode dontAsk --permission-prompts none --allowedTools <list> --max-turns N --max-budget-usd USD --model M
#   --plugin-dir D... --verbose --output-format stream-json
# The tool list is the orchestrator's, one rule per line in T (compiled from the pack's run_headless.sh list,
# the stack's commands and the contract's capabilities; see sdlc_graph/orchestrator/autonomy.py); this script
# holds no list of its own and refuses to run without one. Any other permission mode or prompt policy is refused:
# an unattended step never waits on a prompt.
# plus --settings S (compiled capabilities, denies, hooks) and --session-id UUID. Never --bare (it
# skips hooks, and the hooks are the guard). The stream goes to L, claude's stderr to L.stderr, and R
# is written last: exit_code, duration_seconds, last_assistant_message, subtype, is_error,
# num_turns, total_cost_usd, session_id, permission_denials (run_headless.sh's result-file shape plus session_id and
# the result event's denials, which the orchestrator turns into an escalation).
# --permission-prompts none (Claude Code 2.1.259+): a prompt nobody can answer is denied, not waited on.
# Local vs runner is decided by the orchestrator (credential check, ORCH_EXECUTOR_MODE / ORCH_AUTH_SOURCE in the env);
# the command is the same. Exit: 0 claude exited 0, 1 it did not, 2 misuse.
set -uo pipefail

die() { echo "step_executor: $*" >&2; exit 2; }
WT=""; CMD=""; PROMPT_FILE=""; SETTINGS=""; RESULT=""; LOG=""; SESSION=""; MODEL="sonnet"; TURNS=40; BUDGET=5; REVIEWER=""
TOOLS_FILE=""; PMODE=""; PPROMPTS=""
PLUGIN_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    --worktree) WT="$2"; shift 2 ;;
    --command) CMD="$2"; shift 2 ;;
    --prompt-file) PROMPT_FILE="$2"; shift 2 ;;
    --settings) SETTINGS="$2"; shift 2 ;;
    --result-file) RESULT="$2"; shift 2 ;;
    --log-file) LOG="$2"; shift 2 ;;
    --session-id) SESSION="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --max-turns) TURNS="$2"; shift 2 ;;
    --budget) BUDGET="$2"; shift 2 ;;
    --reviewer) REVIEWER="$2"; shift 2 ;;
    --allowed-tools-file) TOOLS_FILE="$2"; shift 2 ;;
    --permission-mode) PMODE="$2"; shift 2 ;;
    --permission-prompts) PPROMPTS="$2"; shift 2 ;;
    --bare) die "--bare skips hooks, and the hooks are the guard" ;;
    --plugin-dir) [ -e "$2" ] || die "--plugin-dir $2 does not exist (a run without its plugin has no guard)"; PLUGIN_ARGS+=(--plugin-dir "$2"); shift 2 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -d "$WT" ] || die "--worktree $WT is not a directory"
[ -f "$PROMPT_FILE" ] || die "--prompt-file missing"
[ -n "$RESULT" ] && [ -n "$LOG" ] || die "--result-file and --log-file are required"
[[ "$TURNS" =~ ^[0-9]+$ ]] || die "--max-turns must be an integer"
[[ "$BUDGET" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "--budget must be a number"
[ "$PMODE" = "dontAsk" ] || die "--permission-mode must be dontAsk (got '$PMODE')"
[ "$PPROMPTS" = "none" ] || die "--permission-prompts must be none (got '$PPROMPTS')"
[ -f "$TOOLS_FILE" ] && [ -s "$TOOLS_FILE" ] || die "--allowed-tools-file missing or empty (the orchestrator compiles the list)"
command -v claude >/dev/null || die "claude CLI not found"

TOOLS=""
while IFS= read -r rule || [ -n "$rule" ]; do
  [ -n "$rule" ] && TOOLS="${TOOLS:+$TOOLS }$rule"
done < "$TOOLS_FILE"

SETTINGS_ARGS=(); [ -n "$SETTINGS" ] && SETTINGS_ARGS=(--settings "$SETTINGS")
SESSION_ARGS=(); [ -n "$SESSION" ] && SESSION_ARGS=(--session-id "$SESSION")
PROMPT="$(cat "$PROMPT_FILE")"

RC=0
T0=$SECONDS
(cd "$WT" && claude -p --permission-mode dontAsk --permission-prompts none --allowedTools "$TOOLS" --max-turns "$TURNS" \
   --max-budget-usd "$BUDGET" --model "$MODEL" ${SETTINGS_ARGS[@]+"${SETTINGS_ARGS[@]}"} \
   ${SESSION_ARGS[@]+"${SESSION_ARGS[@]}"} ${PLUGIN_ARGS[@]+"${PLUGIN_ARGS[@]}"} \
   --verbose --output-format stream-json "$PROMPT") > "$LOG" 2> "$LOG.stderr" || RC=$?
DURATION=$((SECONDS - T0))

python3 -I - "$LOG" "$RESULT" "$RC" "$DURATION" "$SESSION" <<'EOF' || true
import json, sys
src, dst, rc, dur, session = sys.argv[1:6]
last_text, result = "", {}
try:
    with open(src, errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "assistant":
                content = (ev.get("message") or {}).get("content")
                text = content if isinstance(content, str) else "".join(
                    b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
                if text.strip():
                    last_text = text
            if ev.get("type") == "result":
                result = ev
except OSError as e:
    result = {"error": str(e)}
out = {"exit_code": int(rc), "duration_seconds": int(dur), "last_assistant_message": last_text,
       "subtype": result.get("subtype"), "is_error": result.get("is_error"), "num_turns": result.get("num_turns"),
       "total_cost_usd": result.get("total_cost_usd"), "session_id": result.get("session_id") or session,
       "permission_denials": result.get("permission_denials") or []}
if not last_text and isinstance(result.get("result"), str):
    out["last_assistant_message"] = result["result"]
with open(dst + ".tmp", "w") as fh:
    json.dump(out, fh, indent=2)
    fh.write("\n")
import os
os.replace(dst + ".tmp", dst)
EOF
[ "$RC" -eq 0 ] && exit 0 || exit 1
