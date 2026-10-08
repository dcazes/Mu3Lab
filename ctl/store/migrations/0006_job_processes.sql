CREATE TABLE job_processes (
    pid INTEGER NOT NULL,
    start_ticks INTEGER NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    worker_pid INTEGER NOT NULL,
    worker_start_ticks INTEGER NOT NULL,
    command TEXT NOT NULL,
    deadline_at REAL NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (pid, start_ticks)
);
