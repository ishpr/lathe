"""Dataclasses mirroring the ledger tables (sql/002_ledger.sql), with conversion helpers.

Every public ledger function RETURNS plain dicts (``to_dict()`` output) so results go straight
to JSON for the CLI and MCP; dataclasses are for code inside the ledger package.
``to_dict`` renders uuids as ``str`` and timestamps/dates as ISO-8601 strings.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
import uuid
from typing import Any


def _iso(value: Any) -> Any:
    """ISO-8601 for datetimes/dates, str for uuids, everything else unchanged."""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _sid(value: Any) -> str | None:
    return None if value is None else str(value)


def _pick(cls: type, row: dict[str, Any]) -> dict[str, Any]:
    names = cls.__dataclass_fields__.keys()  # type: ignore[attr-defined]
    return {k: row[k] for k in names if k in row}


@dataclass
class Node:
    """A ledger node (table ``node``). ``state`` must be a state of ``kind``'s machine, or
    ``"active"`` for kinds without one (story, change, decision, rule)."""

    id: str
    kind: str
    slug: str
    title: str
    repo: str
    state: str
    created_by: str
    path: str | None = None
    commit_sha: str | None = None
    tier: str | None = None
    external_id: str | None = None
    touched_symbols: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    acceptance: list[dict[str, Any]] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Node":
        """Build from a ``dict_row`` of ``SELECT * FROM node``. Unknown keys are ignored."""
        data = _pick(cls, row)
        data["id"] = _sid(data.get("id"))
        for key, default in (("touched_symbols", []), ("capabilities", {}), ("acceptance", []), ("attrs", {})):
            if data.get(key) is None:
                data[key] = type(default)()
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict with every field (id as str, timestamps ISO)."""
        return {"id": _sid(self.id), "kind": self.kind, "slug": self.slug, "title": self.title, "repo": self.repo,
                "path": self.path, "commit_sha": self.commit_sha, "state": self.state, "tier": self.tier,
                "external_id": self.external_id, "created_by": self.created_by,
                "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at),
                "touched_symbols": list(self.touched_symbols), "capabilities": dict(self.capabilities),
                "acceptance": list(self.acceptance), "attrs": dict(self.attrs)}


@dataclass
class NodeEdge:
    """A ledger edge (table ``edge``): ``src`` -[rel]-> ``dst`` by node id."""

    src: str
    dst: str
    rel: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "NodeEdge":
        return cls(src=str(row["src"]), dst=str(row["dst"]), rel=row["rel"])

    def to_dict(self) -> dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "rel": self.rel}


@dataclass
class Evidence:
    """An evidence row. ``binding`` is derived: ``source`` starts with ``ci:`` and ``signed_by``
    is non-empty (no real OIDC verification in this increment; see DECISIONS.md)."""

    id: int
    node_id: str
    kind: str
    source: str
    payload: dict[str, Any]
    signed_by: str | None = None
    at: dt.datetime | None = None

    @property
    def binding(self) -> bool:
        return bool(self.source) and self.source.startswith("ci:") and bool((self.signed_by or "").strip())

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Evidence":
        data = _pick(cls, row)
        data["node_id"] = _sid(data.get("node_id"))
        if data.get("payload") is None:
            data["payload"] = {}
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """Includes the derived ``binding`` key."""
        return {"id": self.id, "node_id": _sid(self.node_id), "kind": self.kind, "source": self.source,
                "signed_by": self.signed_by, "payload": self.payload, "at": _iso(self.at), "binding": self.binding}


@dataclass
class Transition:
    """One recorded state change (table ``transition``)."""

    id: int
    node_id: str
    from_state: str | None
    to_state: str
    actor: str
    evidence_ids: list[int] = field(default_factory=list)
    reason: str | None = None
    at: dt.datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Transition":
        data = _pick(cls, row)
        data["node_id"] = _sid(data.get("node_id"))
        data["evidence_ids"] = [int(i) for i in (data.get("evidence_ids") or [])]
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "node_id": _sid(self.node_id), "from_state": self.from_state, "to_state": self.to_state,
                "actor": self.actor, "evidence_ids": list(self.evidence_ids), "reason": self.reason, "at": _iso(self.at)}


@dataclass
class Decision:
    """A decision a human makes first (table ``decision``). Open while ``resolved_at`` is None."""

    id: int
    node_id: str
    question: str
    options: list[Any] = field(default_factory=list)
    recommendation: str | None = None
    proposed_by: str | None = None
    decided_by: str | None = None
    choice: str | None = None
    due: dt.date | None = None
    created_at: dt.datetime | None = None
    resolved_at: dt.datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Decision":
        data = _pick(cls, row)
        data["node_id"] = _sid(data.get("node_id"))
        if data.get("options") is None:
            data["options"] = []
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "node_id": _sid(self.node_id), "question": self.question, "options": self.options,
                "recommendation": self.recommendation, "proposed_by": self.proposed_by, "decided_by": self.decided_by,
                "choice": self.choice, "due": _iso(self.due), "created_at": _iso(self.created_at),
                "resolved_at": _iso(self.resolved_at), "open": self.is_open}


@dataclass
class Run:
    """One agent run (table ``run``): requested vs resolved model, usage, outcome."""

    id: int
    stage: str | None
    requested_model: str
    model_tier: str
    node_id: str | None = None
    resolved_model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    turns: int | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    outcome: str | None = None
    tool_use_id: str | None = None
    agent_type: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    executor: str | None = None
    auth_source: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Run":
        data = _pick(cls, row)
        data["node_id"] = _sid(data.get("node_id"))
        if data.get("cost_usd") is not None:
            data["cost_usd"] = float(data["cost_usd"])
        if data.get("meta") is None:
            data["meta"] = {}
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "node_id": _sid(self.node_id), "stage": self.stage,
                "requested_model": self.requested_model, "resolved_model": self.resolved_model,
                "model_tier": self.model_tier, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "cost_usd": self.cost_usd, "turns": self.turns, "started_at": _iso(self.started_at),
                "finished_at": _iso(self.finished_at), "outcome": self.outcome, "tool_use_id": self.tool_use_id,
                "agent_type": self.agent_type, "meta": dict(self.meta),
                "executor": self.executor, "auth_source": self.auth_source}
