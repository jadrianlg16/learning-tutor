-- Learning Tutor learner store, migration 2 (Stage 1).
--
-- Three additions, all forward-only. Nothing here rewrites an existing row: the events
-- table is append-only and stays that way, so `evaluation_method` is NULL on every event
-- written before this migration ran. A NULL means "written before methods were recorded",
-- not "trusted" — the derivation rule in evidence.py says exactly how those rows count.
--
-- 1. events.evaluation_method — how the answer was judged, adopted after reviewing the
--    open-source Tutor MCP server's tool contract. Only blind_solver | rubric | human can count
--    toward `known`; host_llm is the model grading its own learner and is recorded as
--    self-graded evidence that never reaches mastery.
-- 2. goals.assessment / goals.source_priority — the two goal-contract fields decided
--    2026-09-05 (CONTRACTS.md, `learner` CLI section).
-- 3. idempotency — replay table for mutating calls: same key + same body replays the
--    first response, same key + a different body is a 409.

ALTER TABLE events ADD COLUMN evaluation_method TEXT;

ALTER TABLE goals ADD COLUMN assessment TEXT;
ALTER TABLE goals ADD COLUMN source_priority TEXT;

-- The method an item validation was carried out by. NULL keeps the Stage 0 behaviour
-- (the author/validator identity check is the guarantee); host_llm never promotes.
ALTER TABLE item_validations ADD COLUMN evaluation_method TEXT;

CREATE TABLE IF NOT EXISTS idempotency (
    key         TEXT PRIMARY KEY,
    operation   TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    response    TEXT NOT NULL,   -- JSON: the first response, replayed verbatim
    created_at  TEXT NOT NULL
);
