-- Learning Tutor learner store, migration 4 (exam blueprint and sealed mocks, 2026-09-26).
--
-- Forward-only and additive; the events table stays append-only. What it adds
-- (CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*):
--
-- 1. blueprints — the official item count per concept of a goal (EGEL Plus ISOFT: 143 items
--    over 14 subáreas). A row names its concept by `ref`, the source's own tag ("3.2"),
--    resolved through node_aliases when read: a merge or split moves the aliases, so the
--    row follows the concept without being rewritten.
-- 2. items.pool — `practice` (every item so far) or `mock`: a sealed item that practice,
--    cards and the Anki export never show until it has been answered in a mock exam.

CREATE TABLE IF NOT EXISTS blueprints (
    goal_id    TEXT NOT NULL,
    ref        TEXT NOT NULL,           -- the concept's tag in the source ("3.2")
    area       TEXT NOT NULL,           -- the area code ("3")
    area_title TEXT NOT NULL,
    title      TEXT NOT NULL,           -- the concept's official name, for display
    exam_items INTEGER NOT NULL CHECK (exam_items > 0),
    position   INTEGER NOT NULL,        -- official order
    exam       TEXT,                    -- which exam and edition
    source     TEXT,                    -- where the numbers come from
    updated_at TEXT NOT NULL,
    PRIMARY KEY (goal_id, ref)
);

ALTER TABLE items ADD COLUMN pool TEXT NOT NULL DEFAULT 'practice'
    CHECK (pool IN ('practice', 'mock'));
