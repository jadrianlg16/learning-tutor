"""`learner next` — what to ask, in probe / review / teach mode.

**probe** (KST-style). Candidates are the nodes whose state is ``unknown``. Asking about a
node splits the space of possible knowledge states in two: a pass implies its prerequisites
(its ancestors) too, a fail implies everything downstream stays out of reach. The best
question is the one that splits most evenly, so each candidate scores
``min(#unknown ancestors incl. self, #unknown descendants incl. self)`` and the highest
score wins — ties broken toward nodes whose strict prerequisites are already known, then by
topological order, then by id (so the choice is deterministic). The session's probe budget
(``LT_PROBE_BUDGET``, default 12) caps how many probe-context answers one session may
collect; when it is spent, ``next`` returns no picks and says so.

**review**, in priority order: FSRS-due items, then fragile nodes, then misconception
checks, then transfer variants. This is the same order the Telegram push uses at Stage 1.

**teach**: the lowest ``unknown`` node whose strict prerequisites are all ``known`` — "the
edge" (the zone of proximal development, by name).

Holdout items are never returned in any mode; ``learner holdout-check`` is their only door.
"""

from __future__ import annotations

from typing import Any

from . import evidence as evidence_mod
from . import fsrs_sched
from . import graph as graph_mod
from . import items as items_mod
from . import misconceptions as misc_mod
from .models import Edge, Pick
from .store import LearnerError, Store

MODES = ("probe", "review", "teach", "auto")


def _last_served(store: Store, item_id: str) -> str:
    row = store.one(
        "SELECT MAX(e.ts) AS ts FROM events e JOIN item_versions v "
        "ON v.item_version_id = e.item_version_id WHERE v.item_id = ?",
        (item_id,),
    )
    return (row["ts"] if row and row["ts"] else "") or ""


def pick_item(
    store: Store,
    node_id: str,
    *,
    misconception_claim: str | None = None,
    require_surface_form: bool = False,
    exclude: set[str] | None = None,
) -> Any:
    """Least-recently-served, non-holdout, evidence-writing item on a node."""

    pool = [
        item
        for item in items_mod.for_node(store, node_id, include_holdouts=False)
        if item.item_id not in (exclude or set())
    ]
    if require_surface_form:
        pool = [i for i in pool if i.version and i.version.surface_form]
    if misconception_claim:
        matching = [
            i
            for i in pool
            if i.version and misconception_claim in i.version.distractor_misconceptions.values()
        ]
        pool = matching or pool
    if not pool:
        return None
    pool.sort(key=lambda i: (_last_served(store, i.item_id), i.item_id))
    return pool[0]


def _pick_for_node(
    store: Store,
    mode: str,
    node,
    state,
    reason: str,
    *,
    context: str = "in-session",
    misconception_claim: str | None = None,
    require_surface_form: bool = False,
    exclude: set[str] | None = None,
) -> Pick:
    item = pick_item(
        store,
        node.node_id,
        misconception_claim=misconception_claim,
        require_surface_form=require_surface_form,
        exclude=exclude,
    )
    version = item.version if item else None
    return Pick(
        mode=mode,
        reason=reason if item else f"{reason}; no validated item yet — author and validate one",
        node_id=node.node_id,
        node_title=node.title,
        item_id=item.item_id if item else None,
        item_version_id=item.current_version_id if item else None,
        stem=version.stem if version else None,
        options=version.options if version else [],
        context=context,
    )


def probe_budget_used(store: Store, session_id: str | None) -> int:
    if not session_id:
        return 0
    row = store.one(
        "SELECT COUNT(*) AS n FROM events WHERE session_id = ? AND context = 'probe' "
        "AND kind IN ('answer','probe_answer')",
        (session_id,),
    )
    return int(row["n"] or 0)


def probe_split_scores(
    nodes: list[Any], edges: list[Edge], states: dict[str, Any]
) -> list[tuple[int, str]]:
    unknown = {n.node_id for n in nodes if states[n.node_id].state == "unknown"}
    scored = []
    for nid in unknown:
        anc = (graph_mod.ancestors(edges, nid) & unknown) | {nid}
        desc = (graph_mod.descendants(edges, nid) & unknown) | {nid}
        scored.append((min(len(anc), len(desc)), nid))
    return scored


def next_picks(
    store: Store,
    goal_id: str,
    *,
    mode: str = "auto",
    n: int = 1,
    session_id: str | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise LearnerError(f"mode must be one of {', '.join(MODES)}")
    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    nodes = graph_mod.get_nodes(store, goal_id)
    if not nodes:
        raise LearnerError(f"goal {goal_id!r} has no graph yet: run `learner graph import`")
    node_ids = [node.node_id for node in nodes]
    edges = graph_mod.get_edges(store, node_ids)
    states = evidence_mod.derive_all(store, node_ids)
    order = graph_mod.topological_order(nodes, edges)
    by_id = {node.node_id: node for node in nodes}

    resolved_mode = mode
    if mode == "auto":
        if fsrs_sched.due_items(store, node_ids=node_ids):
            resolved_mode = "review"
        elif any(s.state == "unknown" for s in states.values()) and probe_budget_used(
            store, session_id
        ) < store.settings.probe_budget:
            resolved_mode = "probe"
        else:
            resolved_mode = "teach"

    result: dict[str, Any] = {"goal_id": goal_id, "mode": resolved_mode, "picks": []}
    if resolved_mode == "probe":
        picks, extra = _probe(store, by_id, edges, states, order, n, session_id)
    elif resolved_mode == "review":
        picks, extra = _review(store, by_id, states, order, n, node_ids)
    else:
        picks, extra = _teach(store, by_id, edges, states, order, n)
    result["picks"] = [p.model_dump() for p in picks]
    result.update(extra)
    return result


def _probe(store, by_id, edges, states, order, n, session_id):
    budget = store.settings.probe_budget
    used = probe_budget_used(store, session_id)
    remaining = max(0, budget - used)
    extra = {"probe_budget": budget, "probe_budget_used": used, "probe_budget_remaining": remaining}
    if remaining <= 0:
        extra["note"] = "probe budget spent for this session; switch to teach mode"
        return [], extra

    nodes = list(by_id.values())
    scored = probe_split_scores(nodes, edges, states)
    if not scored:
        extra["note"] = "no unknown nodes left to probe"
        return [], extra
    known = {nid for nid, s in states.items() if s.state == "known"}
    rank = {nid: i for i, nid in enumerate(order)}

    def sort_key(entry):
        score, nid = entry
        prereqs = graph_mod.prerequisites(edges, nid)
        prereqs_known = all(p in known for p in prereqs)
        return (-score, 0 if prereqs_known else 1, rank.get(nid, 0), nid)

    scored.sort(key=sort_key)
    picks = []
    for score, nid in scored[: max(0, min(n, remaining))]:
        picks.append(
            _pick_for_node(
                store,
                "probe",
                by_id[nid],
                states[nid],
                f"splits the unknown set most evenly (split score {score})",
                context="probe",
            )
        )
    return picks, extra


def _review(store, by_id, states, order, n, node_ids):
    picks: list[Pick] = []
    used_items: set[str] = set()
    extra: dict[str, Any] = {}

    for row in fsrs_sched.due_items(store, node_ids=node_ids):
        if len(picks) >= n:
            break
        item = items_mod.get(store, row["item_id"])
        if item.holdout or item.node_id not in by_id:
            continue
        version = item.version
        picks.append(
            Pick(
                mode="review",
                reason=f"FSRS due since {row['due']}",
                node_id=item.node_id,
                node_title=by_id[item.node_id].title,
                item_id=item.item_id,
                item_version_id=item.current_version_id,
                stem=version.stem if version else None,
                options=version.options if version else [],
                context="delayed",
            )
        )
        used_items.add(item.item_id)

    if len(picks) < n:
        for nid in order:
            if len(picks) >= n:
                break
            if nid in by_id and states[nid].state == "fragile":
                picks.append(
                    _pick_for_node(
                        store,
                        "review",
                        by_id[nid],
                        states[nid],
                        "fragile node: " + "; ".join(states[nid].reasons[:2]),
                        exclude=used_items,
                    )
                )

    if len(picks) < n:
        for nid in order:
            if len(picks) >= n:
                break
            claim = states[nid].active_misconception if nid in by_id else None
            if claim:
                picks.append(
                    _pick_for_node(
                        store,
                        "review",
                        by_id[nid],
                        states[nid],
                        f"misconception check: {claim}",
                        misconception_claim=claim,
                        exclude=used_items,
                    )
                )

    if len(picks) < n:
        for nid in order:
            if len(picks) >= n:
                break
            state = states.get(nid)
            if nid in by_id and state and state.state == "known" and state.transfer_passes == 0:
                picks.append(
                    _pick_for_node(
                        store,
                        "review",
                        by_id[nid],
                        state,
                        "known but never tested in a different surface form",
                        context="transfer",
                        require_surface_form=True,
                        exclude=used_items,
                    )
                )
    if not picks:
        extra["note"] = "nothing due, nothing fragile, no misconception, no transfer gap"
    return picks, extra


def _teach(store, by_id, edges, states, order, n):
    known = {nid for nid, s in states.items() if s.state == "known"}
    picks: list[Pick] = []
    extra: dict[str, Any] = {}
    frontier = []
    for nid in order:
        if nid not in by_id or states[nid].state == "known":
            continue
        prereqs = graph_mod.prerequisites(edges, nid)
        if all(p in known for p in prereqs):
            frontier.append(nid)
    if not frontier:
        extra["note"] = "no node has all strict prerequisites known; nothing left to teach"
        return [], extra
    for nid in frontier[:n]:
        state = states[nid]
        reason = "the edge: lowest node whose strict prerequisites are known"
        if state.state == "misconception":
            reason = f"the edge, blocked by a misconception: {state.active_misconception}"
        elif state.state == "fragile":
            reason = "the edge: partially known, prerequisites are known"
        picks.append(_pick_for_node(store, "teach", by_id[nid], state, reason))
    return picks, extra


def has_active_misconception(store: Store, node_id: str) -> bool:
    return misc_mod.active_for_node(store, node_id) is not None
