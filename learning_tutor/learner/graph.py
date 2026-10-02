"""The concept graph: import, show (json / mermaid), and revise with evidence migration.

Node ids are stable (``n_<slug>_<4hex>``) and aliases resolve names to ids, so a graph can
be re-imported or reworded without orphaning evidence.

Evidence migration is append-only, like everything else: a split copies the parent's
evidence events to every child and a merge unions the sources' evidence into the target, in
both cases by *appending* new events whose payload carries
``{"migrated": true, "migrated_from": <old node>, "source_event_id": ..., "original_ts": ...}``.
Nothing is ever rewritten.
"""

from __future__ import annotations

import json
from typing import Any

from . import events as events_mod
from .ids import node_id as make_node_id
from .ids import normalise_alias, prefixed, slugify
from .models import Edge, Node
from .store import LearnerError, Store, utcnow

EDGE_TYPES = {
    "strict_prerequisite",
    "recommended_background",
    "course_sequence",
    "co_requisite",
    "supports",
    "misconception_for",
    "transfer_related",
}
PROVENANCES = {"course", "reference", "model", "learner_evidence", "human"}

#: event kinds that carry evidence about a node and therefore migrate with it
EVIDENCE_KINDS = ["answer", "probe_answer", "teach_back"]


# --------------------------------------------------------------------------- helpers
def bump_version(store: Store, goal_id: str | None, op: str, detail: dict[str, Any]) -> int:
    cur = store.execute(
        "INSERT INTO graph_versions(goal_id, ts, op, detail) VALUES (?,?,?,?)",
        (goal_id, utcnow(), op, json.dumps(detail)),
    )
    return int(cur.lastrowid)


def current_version(store: Store) -> int:
    row = store.one("SELECT MAX(version) AS v FROM graph_versions")
    return int(row["v"] or 0)


def resolve(store: Store, ref: str, *, required: bool = True) -> str | None:
    """Resolve a node id, exact title or alias to a node id."""

    if not ref:
        if required:
            raise LearnerError("empty node reference")
        return None
    row = store.one("SELECT node_id FROM nodes WHERE node_id = ?", (ref,))
    if row:
        return row["node_id"]
    row = store.one("SELECT node_id FROM node_aliases WHERE alias = ?", (normalise_alias(ref),))
    if row:
        return row["node_id"]
    row = store.one("SELECT node_id FROM nodes WHERE title = ?", (ref,))
    if row:
        return row["node_id"]
    if required:
        raise LearnerError(f"unknown node {ref!r}")
    return None


def add_alias(store: Store, node: str, label: str) -> None:
    key = normalise_alias(label)
    if not key:
        return
    existing = store.one("SELECT node_id FROM node_aliases WHERE alias = ?", (key,))
    if existing and existing["node_id"] != node:
        raise LearnerError(f"alias {label!r} already points at {existing['node_id']}")
    if existing:
        return
    store.insert(
        "node_aliases",
        {"alias": key, "node_id": node, "label": label, "created_at": utcnow()},
    )


def create_node(
    store: Store,
    title: str,
    *,
    goal_id: str | None,
    version: int,
    node_id: str | None = None,
    aliases: list[str] | None = None,
    domain: str | None = None,
) -> str:
    nid = node_id or make_node_id(title)
    row = store.one("SELECT node_id, title FROM nodes WHERE node_id = ?", (nid,))
    if row and row["title"] != title:
        # slug hash collision with a different concept: disambiguate deterministically
        suffix = 2
        while store.one("SELECT 1 FROM nodes WHERE node_id = ?", (f"{nid}{suffix}",)):
            suffix += 1
        nid = f"{nid}{suffix}"
        row = None
    if not row:
        store.insert(
            "nodes",
            {
                "node_id": nid,
                "title": title,
                "slug": slugify(title),
                "domain": domain,
                "created_at": utcnow(),
                "graph_version": version,
                "retired": 0,
            },
        )
    add_alias(store, nid, title)
    for alias in aliases or []:
        add_alias(store, nid, alias)
    if goal_id:
        store.execute(
            "INSERT OR IGNORE INTO node_goals(node_id, goal_id) VALUES (?,?)", (nid, goal_id)
        )
    return nid


def add_edge(
    store: Store,
    from_node: str,
    to_node: str,
    *,
    edge_type: str = "strict_prerequisite",
    provenance: str = "model",
    version: int,
) -> str:
    if edge_type not in EDGE_TYPES:
        raise LearnerError(f"unknown edge type {edge_type!r}")
    if provenance not in PROVENANCES:
        raise LearnerError(f"unknown provenance {provenance!r}")
    if from_node == to_node:
        raise LearnerError(f"self edge on {from_node}")
    existing = store.one(
        "SELECT edge_id FROM edges WHERE from_node=? AND to_node=? AND type=? AND retired=0",
        (from_node, to_node, edge_type),
    )
    if existing:
        return existing["edge_id"]
    eid = prefixed("e")
    store.insert(
        "edges",
        {
            "edge_id": eid,
            "from_node": from_node,
            "to_node": to_node,
            "type": edge_type,
            "provenance": provenance,
            "graph_version": version,
            "retired": 0,
        },
    )
    return eid


# --------------------------------------------------------------------------- import
def import_graph(store: Store, goal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    nodes = payload.get("nodes") or []
    edges = payload.get("edges") or []
    if not nodes:
        raise LearnerError("graph import needs at least one node")

    version = bump_version(
        store, goal_id, "import", {"nodes": len(nodes), "edges": len(edges)}
    )
    created: list[str] = []
    for spec in nodes:
        title = spec.get("title") or spec.get("id")
        if not title:
            raise LearnerError("every node needs a title")
        nid = create_node(
            store,
            title,
            goal_id=goal_id,
            version=version,
            node_id=spec.get("id"),
            aliases=spec.get("aliases") or [],
            domain=spec.get("domain"),
        )
        created.append(nid)

    edge_ids = []
    for spec in edges:
        src = resolve(store, spec.get("from", ""))
        dst = resolve(store, spec.get("to", ""))
        edge_ids.append(
            add_edge(
                store,
                src,
                dst,
                edge_type=spec.get("type", "strict_prerequisite"),
                provenance=spec.get("provenance", "model"),
                version=version,
            )
        )
    events_mod.append(
        store,
        kind="graph_revision",
        goal_id=goal_id,
        payload={"op": "import", "version": version, "nodes": created, "edges": len(edge_ids)},
    )
    return {"graph_version": version, "nodes": created, "edges": len(edge_ids)}


# --------------------------------------------------------------------------- read
def get_nodes(
    store: Store, goal_id: str | None = None, *, include_retired: bool = False
) -> list[Node]:
    sql = "SELECT n.* FROM nodes n"
    params: list[Any] = []
    where = []
    if goal_id:
        sql += " JOIN node_goals g ON g.node_id = n.node_id AND g.goal_id = ?"
        params.append(goal_id)
    if not include_retired:
        where.append("n.retired = 0")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY n.rowid ASC"
    out = []
    for row in store.query(sql, params):
        aliases = [
            r["label"]
            for r in store.query(
                "SELECT label FROM node_aliases WHERE node_id = ? ORDER BY created_at",
                (row["node_id"],),
            )
        ]
        goals = [
            r["goal_id"]
            for r in store.query(
                "SELECT goal_id FROM node_goals WHERE node_id = ?", (row["node_id"],)
            )
        ]
        out.append(
            Node(
                node_id=row["node_id"],
                title=row["title"],
                slug=row["slug"],
                domain=row["domain"],
                aliases=aliases,
                goals=goals,
                graph_version=row["graph_version"],
                retired=bool(row["retired"]),
            )
        )
    return out


def get_edges(store: Store, node_ids: list[str] | None = None) -> list[Edge]:
    rows = store.query("SELECT * FROM edges WHERE retired = 0")
    edges = [
        Edge(
            edge_id=r["edge_id"],
            from_node=r["from_node"],
            to_node=r["to_node"],
            type=r["type"],
            provenance=r["provenance"],
            graph_version=r["graph_version"],
            retired=bool(r["retired"]),
        )
        for r in rows
    ]
    if node_ids is None:
        return edges
    keep = set(node_ids)
    return [e for e in edges if e.from_node in keep and e.to_node in keep]


def prerequisites(edges: list[Edge], node_id: str, *, strict_only: bool = True) -> list[str]:
    types = {"strict_prerequisite"} if strict_only else {e.type for e in edges}
    return [e.from_node for e in edges if e.to_node == node_id and e.type in types]


def topological_order(nodes: list[Node], edges: list[Edge]) -> list[str]:
    ids = [n.node_id for n in nodes]
    incoming = {nid: set() for nid in ids}
    for e in edges:
        if e.type == "strict_prerequisite" and e.to_node in incoming and e.from_node in incoming:
            incoming[e.to_node].add(e.from_node)
    ordered: list[str] = []
    remaining = dict(incoming)
    while remaining:
        ready = sorted(nid for nid, deps in remaining.items() if not (deps - set(ordered)))
        if not ready:  # cycle: fall back to id order for the rest, deterministically
            ready = sorted(remaining)
        for nid in ready:
            ordered.append(nid)
            remaining.pop(nid, None)
    return ordered


def ancestors(edges: list[Edge], node_id: str) -> set[str]:
    """All strict prerequisites of ``node_id``, transitively."""

    seen: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        for e in edges:
            if e.type == "strict_prerequisite" and e.to_node == cur and e.from_node not in seen:
                seen.add(e.from_node)
                stack.append(e.from_node)
    return seen


def descendants(edges: list[Edge], node_id: str) -> set[str]:
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        for e in edges:
            if e.type == "strict_prerequisite" and e.from_node == cur and e.to_node not in seen:
                seen.add(e.to_node)
                stack.append(e.to_node)
    return seen


MERMAID_CLASSDEFS = [
    "classDef unknown fill:#f4f4f5,stroke:#a1a1aa,color:#3f3f46;",
    "classDef fragile fill:#fef3c7,stroke:#d97706,color:#78350f;",
    "classDef known fill:#dcfce7,stroke:#16a34a,color:#14532d;",
    "classDef misconception fill:#fee2e2,stroke:#dc2626,color:#7f1d1d;",
]
_ARROWS = {
    "strict_prerequisite": "-->",
    "recommended_background": "-.->",
    "course_sequence": "==>",
    "co_requisite": "---",
    "supports": "-.->",
    "misconception_for": "-.->",
    "transfer_related": "-.-",
}


def to_mermaid(nodes: list[Node], edges: list[Edge], states: dict[str, str]) -> str:
    lines = ["graph TD"]
    for node in nodes:
        label = node.title.replace('"', "'")
        lines.append(f'    {node.node_id}["{label}"]')
    for edge in edges:
        arrow = _ARROWS.get(edge.type, "-->")
        lines.append(f"    {edge.from_node} {arrow}|{edge.type}| {edge.to_node}")
    for classdef in MERMAID_CLASSDEFS:
        lines.append(f"    {classdef}")
    by_state: dict[str, list[str]] = {}
    for node in nodes:
        by_state.setdefault(states.get(node.node_id, "unknown"), []).append(node.node_id)
    for state in ("unknown", "fragile", "known", "misconception"):
        members = by_state.get(state)
        if members:
            lines.append(f"    class {','.join(members)} {state};")
    return "\n".join(lines)


# --------------------------------------------------------------------------- revise
def _evidence_events(store: Store, node: str) -> list[Any]:
    marks = ", ".join("?" for _ in EVIDENCE_KINDS)
    return store.query(
        f"SELECT * FROM events WHERE node_id = ? AND kind IN ({marks}) ORDER BY ts",
        (node, *EVIDENCE_KINDS),
    )


def migrate_evidence(store: Store, source: str, targets: list[str], op: str) -> int:
    """Copy every evidence event on ``source`` onto each target, flagged as migrated."""

    copied = 0
    for row in _evidence_events(store, source):
        payload = json.loads(row["payload"]) if row["payload"] else {}
        if payload.get("migrated"):
            payload = {k: v for k, v in payload.items() if k != "migrated"}
        for target in targets:
            events_mod.append(
                store,
                kind=row["kind"],
                session_id=row["session_id"],
                goal_id=row["goal_id"],
                node_id=target,
                item_version_id=row["item_version_id"],
                response=row["response"],
                correct=row["correct"],
                confidence=row["confidence"],
                idk=bool(row["idk"]),
                assistance_level=row["assistance_level"],
                channel=row["channel"],
                context=row["context"],
                prompt_version=row["prompt_version"],
                grader_version=row["grader_version"],
                payload={
                    **payload,
                    "migrated": True,
                    "migration_op": op,
                    "migrated_from": source,
                    "source_event_id": row["event_id"],
                    "original_ts": row["ts"],
                },
            )
            copied += 1
    return copied


def _copy_edges(store: Store, source: str, target: str, version: int) -> None:
    for row in store.query(
        "SELECT * FROM edges WHERE retired = 0 AND (from_node = ? OR to_node = ?)",
        (source, source),
    ):
        src = target if row["from_node"] == source else row["from_node"]
        dst = target if row["to_node"] == source else row["to_node"]
        if src == dst:
            continue
        add_edge(
            store, src, dst, edge_type=row["type"], provenance=row["provenance"], version=version
        )


def _move_tables(store: Store, source: str, target: str) -> None:
    """A study table filed under a concept follows it, like its items do."""

    store.execute("UPDATE study_tables SET node_id = ? WHERE node_id = ?", (target, source))


def _retire(store: Store, node: str, reason: str) -> None:
    store.update("nodes", {"node_id": node}, {"retired": 1, "retired_reason": reason})
    store.execute("UPDATE edges SET retired = 1 WHERE from_node = ? OR to_node = ?", (node, node))


def revise(store: Store, goal_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    if not ops:
        raise LearnerError("no ops given")
    results = []
    for spec in ops:
        op = spec.get("op")
        if op == "add":
            results.append(_op_add(store, goal_id, spec))
        elif op == "remove":
            results.append(_op_remove(store, goal_id, spec))
        elif op == "split":
            results.append(_op_split(store, goal_id, spec))
        elif op == "merge":
            results.append(_op_merge(store, goal_id, spec))
        else:
            raise LearnerError(f"unknown graph op {op!r}")
    return {"ops": results, "graph_version": current_version(store)}


def _op_add(store: Store, goal_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    node_spec = spec.get("node") or {}
    title = node_spec.get("title")
    if not title:
        raise LearnerError("add needs node.title")
    version = bump_version(store, goal_id, "add", {"title": title})
    nid = create_node(
        store,
        title,
        goal_id=goal_id,
        version=version,
        node_id=node_spec.get("id"),
        aliases=node_spec.get("aliases") or [],
    )
    for edge in spec.get("edges") or []:
        add_edge(
            store,
            resolve(store, edge["from"]),
            resolve(store, edge["to"]),
            edge_type=edge.get("type", "strict_prerequisite"),
            provenance=edge.get("provenance", "model"),
            version=version,
        )
    events_mod.append(
        store,
        kind="graph_revision",
        goal_id=goal_id,
        node_id=nid,
        payload={"op": "add", "version": version},
    )
    return {"op": "add", "node": nid, "graph_version": version}


def _op_remove(store: Store, goal_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    nid = resolve(store, spec.get("node", ""))
    version = bump_version(store, goal_id, "remove", {"node": nid})
    _retire(store, nid, spec.get("reason") or "removed by revision")
    events_mod.append(
        store,
        kind="graph_revision",
        goal_id=goal_id,
        node_id=nid,
        payload={"op": "remove", "version": version, "reason": spec.get("reason")},
    )
    return {"op": "remove", "node": nid, "graph_version": version}


def _op_split(store: Store, goal_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    parent = resolve(store, spec.get("node", ""))
    into = spec.get("into") or []
    if len(into) < 2:
        raise LearnerError("split needs at least two children")
    version = bump_version(store, goal_id, "split", {"node": parent, "into": len(into)})
    children = []
    for child_spec in into:
        title = child_spec.get("title")
        if not title:
            raise LearnerError("every split child needs a title")
        children.append(
            create_node(
                store,
                title,
                goal_id=goal_id,
                version=version,
                node_id=child_spec.get("id"),
                aliases=child_spec.get("aliases") or [],
            )
        )
    for child in children:
        _copy_edges(store, parent, child, version)
    # chain the children in the given order as strict prerequisites
    if spec.get("chain", True):
        for left, right in zip(children, children[1:], strict=False):
            add_edge(store, left, right, version=version)
    copied = migrate_evidence(store, parent, children, "split")
    _reassign_items(store, parent, children, spec.get("items"))
    _move_tables(store, parent, children[0])
    _retire(store, parent, "split")
    events_mod.append(
        store,
        kind="graph_revision",
        goal_id=goal_id,
        node_id=parent,
        payload={
            "op": "split",
            "version": version,
            "children": children,
            "evidence_events_copied": copied,
        },
    )
    return {
        "op": "split",
        "node": parent,
        "children": children,
        "evidence_events_copied": copied,
        "graph_version": version,
    }


def _reassign_items(
    store: Store, parent: str, children: list[str], mapping: dict[str, list[str]] | None
) -> None:
    assigned: dict[str, str] = {}
    for ref, item_ids in (mapping or {}).items():
        target = resolve(store, ref)
        for item in item_ids:
            assigned[item] = target
    rows = store.query("SELECT item_id FROM items WHERE node_id = ?", (parent,))
    for row in rows:
        target = assigned.get(row["item_id"], children[0])
        store.update("items", {"item_id": row["item_id"]}, {"node_id": target})


def _op_merge(store: Store, goal_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    sources = [resolve(store, ref) for ref in (spec.get("nodes") or [])]
    if len(sources) < 2:
        raise LearnerError("merge needs at least two nodes")
    into = spec.get("into") or {}
    title = into.get("title") or store.one(
        "SELECT title FROM nodes WHERE node_id = ?", (sources[0],)
    )["title"]
    version = bump_version(store, goal_id, "merge", {"nodes": sources, "title": title})
    target = create_node(
        store,
        title,
        goal_id=goal_id,
        version=version,
        node_id=into.get("id"),
        aliases=into.get("aliases") or [],
    )
    copied = 0
    for source in sources:
        if source == target:
            continue
        _copy_edges(store, source, target, version)
        copied += migrate_evidence(store, source, [target], "merge")
        # every name that used to reach a source now reaches the merged node
        store.execute("UPDATE node_aliases SET node_id = ? WHERE node_id = ?", (target, source))
        row = store.one("SELECT title FROM nodes WHERE node_id = ?", (source,))
        if row:
            add_alias(store, target, row["title"])
        store.execute("UPDATE items SET node_id = ? WHERE node_id = ?", (target, source))
        _move_tables(store, source, target)
        _retire(store, source, "merged")
    events_mod.append(
        store,
        kind="graph_revision",
        goal_id=goal_id,
        node_id=target,
        payload={
            "op": "merge",
            "version": version,
            "sources": sources,
            "evidence_events_copied": copied,
        },
    )
    return {
        "op": "merge",
        "node": target,
        "sources": sources,
        "evidence_events_copied": copied,
        "graph_version": version,
    }
