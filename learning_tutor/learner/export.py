"""`events.jsonl` — an export of the SQLite event table, never the truth.

One JSON object per line, in event order, with the payload inlined as an object. The file
is rewritten from scratch on every export so it can never disagree with the database.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from .store import Store

#: The members of the passport zip, in CONTRACTS.md's order (*Gateway response shapes*,
#: ``GET /api/passport``). Every one is **always present**: an empty member named in
#: ``MANIFEST.json`` is honest, a silently missing one is not.
PASSPORT_MEMBERS = (
    "events.jsonl",
    "state.json",
    "learner.md",
    "notes.md",
    "goals.json",
    "graph.json",
    "disputes.json",
)


def export_events(store: Store, out: str | Path | None = None) -> dict[str, Any]:
    path = Path(out) if out else store.settings.export_path
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in store.query("SELECT * FROM events ORDER BY ts ASC, event_id ASC"):
            record = dict(row)
            record["payload"] = json.loads(record["payload"]) if record["payload"] else None
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return {"out": str(path), "events": count}


def export_passport(store: Store, goal_id: str | None = None) -> dict[str, Any]:
    """Everything about the learner in one JSON object (the Stage 2 'learner passport')."""

    tables = [
        "goals",
        "nodes",
        "node_aliases",
        "edges",
        "graph_versions",
        "items",
        "item_versions",
        "item_validations",
        "holdouts",
        "sessions",
        "misconceptions",
        "disputes",
        "events",
    ]
    data: dict[str, Any] = {}
    for table in tables:
        data[table] = [dict(r) for r in store.query(f"SELECT * FROM {table}")]
    if goal_id:
        data["goals"] = [g for g in data["goals"] if g["goal_id"] == goal_id]
    return data


def _events_jsonl(store: Store) -> str:
    """The event table as JSON lines, without writing ``events.jsonl`` to disk.

    :func:`export_events` rewrites the file; a passport must not have that side effect —
    an export is a read.
    """

    lines = []
    for row in store.query("SELECT * FROM events ORDER BY ts ASC, event_id ASC"):
        record = dict(row)
        record["payload"] = json.loads(record["payload"]) if record["payload"] else None
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def passport_zip(store: Store, goal_id: str | None = None) -> bytes:
    """The learner passport as a zip: "the model-independence promise made concrete".

    Built by the service that owns the volume, because every member is on that volume:
    the event table, the derived files, the vault's ``learner.md`` and session logs. A
    caller on another filesystem gets the same bytes over HTTP rather than a partial
    export assembled from whatever it could reach.
    """

    from . import api  # local: api imports this module at load time

    goals = [dict(row) for row in store.query("SELECT * FROM goals ORDER BY created_at")]
    if goal_id:
        goals = [g for g in goals if g["goal_id"] == goal_id]

    manifest: dict[str, Any] = {
        "generated_by": "learner-svc",
        "goal": goal_id,
        "schema_version": store.schema_version(),
        "members": {
            "events.jsonl": "events table",
            "state.json": "derived, regenerated for this export",
            "learner.md": "vault learner.md, regenerated for this export",
            "notes.md": "LT_DATA_DIR/learner/notes.md",
            "goals.json": "goals table",
            "graph.json": "nodes + edges + derived node state, per goal",
            "disputes.json": "disputes table",
        },
    }

    contents: dict[str, str] = {}
    contents["events.jsonl"] = _events_jsonl(store)
    contents["goals.json"] = json.dumps({"goals": goals}, ensure_ascii=False, indent=2, default=str)

    graphs: dict[str, Any] = {}
    learner_md_parts: list[str] = []
    for row in goals:
        gid = str(row["goal_id"])
        graphs[gid] = api.graph_show(store, gid, "json")
        learner_md_parts.append(api.summary(store, gid, "md")["markdown"])
    contents["graph.json"] = json.dumps(graphs, ensure_ascii=False, indent=2, default=str)

    from . import views  # local, for the same reason

    views.write_state(store)
    contents["state.json"] = _read(store.settings.state_path)
    contents["notes.md"] = _read(store.settings.notes_path)
    # `api.summary` rewrote learner.md for the last goal; the file is the truth when it
    # exists, and the concatenated summaries are the fallback for a store with no vault.
    contents["learner.md"] = _read(store.settings.learner_md_path) or "\n\n---\n\n".join(
        learner_md_parts
    )
    contents["disputes.json"] = json.dumps(
        api.disputes(store, goal_id=goal_id)["disputes"], ensure_ascii=False, indent=2, default=str
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in PASSPORT_MEMBERS:
            archive.writestr(name, contents.get(name, ""))
        sessions_dir = store.settings.sessions_dir
        logs = sorted(sessions_dir.glob("*.md")) if sessions_dir.is_dir() else []
        for path in logs:
            archive.writestr(f"artifacts/sessions/{path.name}", _read(path))
        if not logs:
            archive.writestr("artifacts/.keep", "")
        manifest["members"]["artifacts/sessions/"] = f"{len(logs)} session log(s) from the vault"
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue()
