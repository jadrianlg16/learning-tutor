-- Learning Tutor learner store, migration 3 (study tools, 2026-09-24).
--
-- Forward-only, additive, and nothing here rewrites an event: the events table stays
-- append-only. What it adds (CONTRACTS.md, *Study tools*):
--
-- 1. goals.sessions_per_week — the cadence the feasibility line divides by. Until now it
--    lived only in the gateway's state file, so learner.md divided by LT_SESSIONS_PER_WEEK
--    and the web UI by the contract value: two different answers to "does this fit?".
-- 2. items.source_key — a content hash for anything an importer wrote, so importing the
--    same file twice adds nothing. NULL for items authored one at a time.
-- 3. item_versions.explanation / .source — why the key is right (shown only after an
--    attempt) and where the question came from.
-- 4. study_tables — comparison and definition tables. Reference material, not evidence:
--    nothing about the learner is stored here.

ALTER TABLE goals ADD COLUMN sessions_per_week INTEGER;

ALTER TABLE items ADD COLUMN source_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_source_key
    ON items(source_key) WHERE source_key IS NOT NULL;

ALTER TABLE item_versions ADD COLUMN explanation TEXT;
ALTER TABLE item_versions ADD COLUMN source TEXT;

CREATE TABLE IF NOT EXISTS study_tables (
    table_id       TEXT PRIMARY KEY,
    goal_id        TEXT NOT NULL,
    node_id        TEXT,                 -- the concept it belongs to, when one is known
    title          TEXT NOT NULL,
    columns        TEXT NOT NULL,        -- JSON list of header cells
    rows           TEXT NOT NULL,        -- JSON list of lists, each as long as columns
    source         TEXT,                 -- file#heading, or who wrote it
    author         TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    retired        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_study_tables_content
    ON study_tables(goal_id, content_sha256);
