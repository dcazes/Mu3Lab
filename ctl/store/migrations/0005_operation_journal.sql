CREATE TABLE operations (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    service_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('backup', 'restore', 'update')),
    schema_version INTEGER NOT NULL DEFAULT 1,
    phase TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('active', 'succeeded', 'failed', 'rolled_back', 'cancelled', 'needs_attention', 'resolved')
    ),
    was_running INTEGER NOT NULL CHECK (was_running IN (0, 1)),
    previous_release TEXT NOT NULL DEFAULT '',
    target_release TEXT NOT NULL DEFAULT '',
    chosen_snapshot TEXT NOT NULL DEFAULT '',
    recovery_snapshot TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX operations_one_active ON operations(service_id) WHERE state = 'active';
CREATE INDEX operations_unresolved ON operations(service_id, state);
CREATE TABLE operation_steps (
    operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    phase TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('begin', 'resumed', 'finished')),
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (operation_id, seq)
);
