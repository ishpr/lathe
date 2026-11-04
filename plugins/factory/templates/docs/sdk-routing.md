# Model routing outside the interactive session

The spawn guard (`hooks/guard_agent_spawn.py`) pins the model of every `Agent` tool call in an
interactive or `claude -p` session. Code that drives Claude through the Agent SDK or a Workflow does
not pass through that hook the same way, so it must **pass the model explicitly** on every agent and
cap spend. The tiers are in `sdlc_graph/ledger/routing_policy.json`; the version pins and the
`availableModels` ceiling are in `managed-settings.example.json`.

| Stage | Tier | Alias |
|---|---|---|
| epic.decompose, contract.design.high, postmortem | planner | `fable` |
| contract.design, review.synthesis | orchestrator | `opus` |
| tests, implement, review.lens | executor | `sonnet` |
| triage, classify, summarise | utility | `haiku` |

## Agent SDK (Python)

```python
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, query

options = ClaudeAgentOptions(
    model="opus",                      # the orchestrator session
    max_budget_usd=5.00,               # client-side estimate; a hard stop for this run
    max_turns=40,
    agents={
        "test-author": AgentDefinition(description="Writes failing tests", prompt="...", tools=["Read", "Grep", "Glob", "Write", "Edit"], model="sonnet"),
        "reviewer": AgentDefinition(description="Reviews a diff", prompt="...", tools=["Read", "Grep", "Glob"], model="sonnet"),
        "planner": AgentDefinition(description="Plans an epic", prompt="...", tools=["Read", "Grep", "Glob"], model="opus"),
        "triage": AgentDefinition(description="Cheap sweeps", prompt="...", tools=["Read", "Grep", "Glob"], model="haiku"),
    },
)
async for message in query(prompt="...", options=options):
    ...
```

An `AgentDefinition` without `model` inherits the parent's, which is the expensive default.

## Workflow script

```js
const hits = await agent("List every caller of parse_ticket", { model: "haiku" })
const plan = await agent("Decompose this epic into tickets: ...", { model: "opus" })
```

An `agent()` call without `{ model }` runs on the session model. Set it on each call.

## Watching it

`PreModelSwitch` and `PostModelSwitch` hooks append each change to `.claude/work/model_switches.jsonl`.
The run recorder writes the model that actually answered each subagent to the ledger.
