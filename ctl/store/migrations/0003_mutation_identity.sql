ALTER TABLE jobs ADD COLUMN actor_subject TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN request_namespace TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN request_hash TEXT NOT NULL DEFAULT '';
DROP INDEX jobs_idempotency_key;
CREATE TABLE mutation_requests (
    actor_subject TEXT NOT NULL,
    namespace TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    PRIMARY KEY (actor_subject, namespace, idempotency_key)
);
CREATE INDEX mutation_requests_job ON mutation_requests(job_id);
DROP INDEX install_batches_idempotency;
CREATE TABLE batch_requests (
    owner_uid TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES install_batches(id),
    PRIMARY KEY (owner_uid, idempotency_key)
);
