CREATE TABLE IF NOT EXISTS groups (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pin           CHAR(5) UNIQUE NOT NULL,
    question      TEXT NOT NULL,
    creator_token TEXT NOT NULL,
    expected_count INT NULL,
    deadline      TIMESTAMPTZ NULL,
    status        TEXT NOT NULL DEFAULT 'collecting',
    consensus     TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS participants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    nickname    TEXT NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, nickname)
);
CREATE TABLE IF NOT EXISTS responses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id        UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    participant_id  UUID NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, participant_id)
);
CREATE INDEX IF NOT EXISTS responses_group_id_idx ON responses (group_id);
