-- Learning Tutor learner store, migration 1.
-- The events table is the truth; everything else is either input (goals, graph, items)
-- or a cache that can be rebuilt from events. events.jsonl is an export of this file.

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    goal_id             TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    depth               TEXT NOT NULL DEFAULT 'explain',
    deadline            TEXT,
    minutes_per_session INTEGER,
    purpose             TEXT,
    contract            TEXT,             -- JSON: goal contract, filled in later stages
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_versions (
    version    INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id    TEXT,
    ts         TEXT NOT NULL,
    op         TEXT NOT NULL,             -- import|add|remove|split|merge
    detail     TEXT                       -- JSON
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id        TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    slug           TEXT NOT NULL,
    domain         TEXT,
    created_at     TEXT NOT NULL,
    graph_version  INTEGER NOT NULL,
    retired        INTEGER NOT NULL DEFAULT 0,
    retired_reason TEXT
);

CREATE TABLE IF NOT EXISTS node_goals (
    node_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    PRIMARY KEY (node_id, goal_id)
);

CREATE TABLE IF NOT EXISTS node_aliases (
    alias      TEXT PRIMARY KEY,          -- normalised alias text
    node_id    TEXT NOT NULL,
    label      TEXT NOT NULL,             -- alias as written
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id       TEXT PRIMARY KEY,
    from_node     TEXT NOT NULL,
    to_node       TEXT NOT NULL,
    type          TEXT NOT NULL,          -- strict_prerequisite|recommended_background|...
    provenance    TEXT NOT NULL,          -- course|reference|model|learner_evidence|human
    graph_version INTEGER NOT NULL,
    retired       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node);

CREATE TABLE IF NOT EXISTS items (
    item_id            TEXT PRIMARY KEY,
    node_id            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'TEACHING_ONLY',
    current_version_id TEXT,
    author             TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    holdout            INTEGER NOT NULL DEFAULT 0,
    retired            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_node ON items(node_id);

CREATE TABLE IF NOT EXISTS item_versions (
    item_version_id           TEXT PRIMARY KEY,
    item_id                   TEXT NOT NULL,
    version                   INTEGER NOT NULL,
    stem                      TEXT NOT NULL,
    options                   TEXT NOT NULL,   -- JSON list
    answer                    TEXT NOT NULL,
    distractor_misconceptions TEXT NOT NULL,   -- JSON object {option: misconception}
    kind                      TEXT NOT NULL DEFAULT 'mc',
    components                TEXT NOT NULL,   -- JSON list of knowledge components
    surface_form              TEXT,            -- for transfer variants
    author                    TEXT NOT NULL,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_versions_item ON item_versions(item_id);

CREATE TABLE IF NOT EXISTS item_validations (
    validation_id   TEXT PRIMARY KEY,
    item_id         TEXT NOT NULL,
    item_version_id TEXT NOT NULL,
    validator       TEXT NOT NULL,       -- model or solver identity; never the author
    result          TEXT NOT NULL,       -- pass|fail
    notes           TEXT,
    ts              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holdouts (
    item_id         TEXT PRIMARY KEY,
    assigned_at     TEXT NOT NULL,
    last_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS fsrs_state (
    item_id    TEXT PRIMARY KEY,
    card       TEXT NOT NULL,           -- JSON: fsrs.Card.to_dict()
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    goal_id    TEXT,
    channel    TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    summary    TEXT
);

CREATE TABLE IF NOT EXISTS misconceptions (
    misconception_id  TEXT PRIMARY KEY,
    node_id           TEXT NOT NULL,
    claim             TEXT NOT NULL,
    state             TEXT NOT NULL,     -- suspected|active|weakened|resolved|recurred
    steps             TEXT NOT NULL,     -- JSON list of {step,outcome,ts}
    resolution_reason TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_misconceptions_node ON misconceptions(node_id);

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id  TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    node_id     TEXT,
    item_id     TEXT,
    note        TEXT,
    status      TEXT NOT NULL,           -- open|settled
    outcome     TEXT,                    -- upheld|rejected
    evidence    TEXT,
    check_items TEXT,                    -- JSON list of item ids scheduled as a check
    created_at  TEXT NOT NULL,
    settled_at  TEXT
);

-- The truth. Append-only: enforced by triggers below.
CREATE TABLE IF NOT EXISTS events (
    event_id         TEXT PRIMARY KEY,
    ts               TEXT NOT NULL,
    session_id       TEXT,
    goal_id          TEXT,
    node_id          TEXT,
    item_version_id  TEXT,
    kind             TEXT NOT NULL,
    response         TEXT,
    correct          INTEGER,
    confidence       INTEGER,
    idk              INTEGER NOT NULL DEFAULT 0,
    assistance_level INTEGER NOT NULL DEFAULT 0,
    channel          TEXT NOT NULL DEFAULT 'claude-code',
    context          TEXT NOT NULL DEFAULT 'in-session',
    prompt_version   TEXT,
    grader_version   TEXT,
    payload          TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only: UPDATE is not allowed');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events is append-only: DELETE is not allowed');
END;
