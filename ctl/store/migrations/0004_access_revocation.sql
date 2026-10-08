CREATE TABLE access_holds (
    subject TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('deactivated', 'demoted')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
CREATE TABLE access_revocations (
    subject TEXT NOT NULL REFERENCES access_holds(subject) ON DELETE CASCADE,
    target TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'done')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (subject, target)
);
CREATE INDEX access_revocations_due ON access_revocations(state, next_attempt_at);
