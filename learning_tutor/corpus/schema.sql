-- Corpus store: the material the tutor teaches from and cites against.
-- Migration 1. Idempotent: every statement is CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS sources (
    source_id   TEXT PRIMARY KEY,
    goal_id     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('alignment', 'authority', 'learner')),
    kind        TEXT NOT NULL CHECK (kind IN ('pdf', 'docx', 'pptx', 'md', 'txt', 'audio', 'youtube', 'url')),
    title       TEXT NOT NULL,
    path_or_url TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);

-- Dedupe key: the same bytes ingested twice for one goal is one source.
CREATE UNIQUE INDEX IF NOT EXISTS sources_goal_sha256 ON sources (goal_id, sha256);
CREATE INDEX IF NOT EXISTS sources_goal_role ON sources (goal_id, role);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL REFERENCES sources (source_id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    locator    TEXT NOT NULL DEFAULT '{}',  -- JSON: {"page":14} {"slide":3} {"t":"00:12:31"} {"heading":"..."}
    text       TEXT NOT NULL,
    tokens_est INTEGER NOT NULL DEFAULT 0,
    flags      TEXT NOT NULL DEFAULT '[]'   -- JSON array from sanitize.scan()
);

CREATE INDEX IF NOT EXISTS chunks_source ON chunks (source_id, ordinal);

-- External-content FTS5: the text lives in `chunks`, the index mirrors it via triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    text,
    content = 'chunks',
    content_rowid = 'rowid',
    tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts (rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts (chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO chunks_fts (rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id TEXT NOT NULL REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL,      -- float32 little-endian, `dim` values
    PRIMARY KEY (chunk_id, model)
);

-- The document's own outline. The plan phase uses it as a prior for the concept graph.
CREATE TABLE IF NOT EXISTS structure (
    source_id TEXT NOT NULL REFERENCES sources (source_id) ON DELETE CASCADE,
    ordinal   INTEGER NOT NULL,
    level     INTEGER NOT NULL,
    title     TEXT NOT NULL,
    locator   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source_id, ordinal)
);

-- Proposed / approved source lists from the research pass. The web research itself is done
-- by the harness or the gateway; this module only stores what the learner approved.
CREATE TABLE IF NOT EXISTS research_lists (
    list_id      TEXT PRIMARY KEY,
    goal_id      TEXT NOT NULL,
    topic        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    created_at   TEXT NOT NULL,
    decided_at   TEXT,
    payload      TEXT NOT NULL DEFAULT '{}'  -- JSON: {"sources": [...]}
);

CREATE INDEX IF NOT EXISTS research_goal ON research_lists (goal_id, created_at);
