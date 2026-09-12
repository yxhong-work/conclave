-- 002_rounds.sql — multi-round deliberation (SPEC docs/MULTIROUND_SPEC.md §3)
-- Idempotent: every statement is guarded; safe to re-run on fresh or populated DB.
-- Runs after 001_init.sql via init_db's sorted glob (db.py).

-- ---------------------------------------------------------------------------
-- 1. responses: add round dimension to the composite key
-- ---------------------------------------------------------------------------
ALTER TABLE responses ADD COLUMN IF NOT EXISTS round_number INT NOT NULL DEFAULT 1;
-- Existing rows get round_number=1; they already satisfy UNIQUE(group_id, participant_id),
-- so the new 3-column key is also satisfiable — no data conflict.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conname = 'responses_group_id_round_number_participant_id_key') THEN
    ALTER TABLE responses DROP CONSTRAINT IF EXISTS responses_group_id_participant_id_key;
    ALTER TABLE responses ADD CONSTRAINT responses_group_id_round_number_participant_id_key
      UNIQUE (group_id, round_number, participant_id);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS responses_group_round_idx ON responses (group_id, round_number);

-- ---------------------------------------------------------------------------
-- 2. groups: add round tracking
-- ---------------------------------------------------------------------------
ALTER TABLE groups ADD COLUMN IF NOT EXISTS current_round INT NOT NULL DEFAULT 1;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS max_rounds   INT NOT NULL DEFAULT 3;

-- ---------------------------------------------------------------------------
-- 2a. participants: stable member_seq for cross-round stance alignment (PRIVATE track only)
-- ---------------------------------------------------------------------------
ALTER TABLE participants ADD COLUMN IF NOT EXISTS member_seq INT NULL;
-- Backfill: rank by joined_at within each group, starting at 1.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='participants' AND column_name='member_seq')
     AND EXISTS (SELECT 1 FROM participants WHERE member_seq IS NULL) THEN
    WITH ranked AS (
      SELECT id, row_number() OVER (PARTITION BY group_id ORDER BY joined_at, id) AS rn
      FROM participants
    )
    UPDATE participants p SET member_seq = ranked.rn
    FROM ranked WHERE p.id = ranked.id;
  END IF;
END $$;
-- Enforce uniqueness within a group once backfilled (member_seq is system-internal,
-- never exposed; a UNIQUE constraint prevents race-induced duplicates on concurrent joins).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
    WHERE conname = 'participants_group_id_member_seq_key') THEN
    ALTER TABLE participants ADD CONSTRAINT participants_group_id_member_seq_key
      UNIQUE (group_id, member_seq);
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. rounds: per-round history (public consensus + private stance_digest + group shift summary)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rounds (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id       UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    round_number   INT NOT NULL,
    question       TEXT NOT NULL,
    consensus      TEXT NULL,            -- public: group-level, no per-member attribution
    stance_digest  TEXT NULL,            -- PRIVATE: per-member stances, stance call only, never public (§5.6)
    stance_shift_summary TEXT NULL,     -- public-safe: group-level evolution summary, no member labels (§5.1)
    research_brief TEXT NULL,           -- cached MCP brief for cross-round cost control (MCP_SPEC §17 extension)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    analyzed_at    TIMESTAMPTZ NULL,
    UNIQUE (group_id, round_number)
);
CREATE INDEX IF NOT EXISTS rounds_group_id_idx ON rounds (group_id);

-- ---------------------------------------------------------------------------
-- 4. Backfill existing single-round groups into rounds table (idempotent)
-- ---------------------------------------------------------------------------
INSERT INTO rounds (group_id, round_number, question, consensus)
SELECT id, 1, question, consensus FROM groups g
WHERE NOT EXISTS (SELECT 1 FROM rounds r WHERE r.group_id = g.id AND r.round_number = 1)
ON CONFLICT (group_id, round_number) DO NOTHING;
