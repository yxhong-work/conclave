-- 003_adaptive_questions.sql (SPEC docs/ADAPTIVE_SPEC.md §5)
-- Adaptive per-member questioning: per-group opt-in flag + member_questions
-- (one question per member per round, visible ONLY to the recipient).
-- Idempotent, statement-guarded like 001/002.

ALTER TABLE groups ADD COLUMN IF NOT EXISTS adaptive_questions BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS member_questions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id       UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    round_number   INT NOT NULL,                      -- answering round (= N+1, generated during round N's analysis)
    participant_id UUID NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    question       TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, round_number, participant_id)
);

CREATE INDEX IF NOT EXISTS member_questions_group_round_idx
    ON member_questions (group_id, round_number);
