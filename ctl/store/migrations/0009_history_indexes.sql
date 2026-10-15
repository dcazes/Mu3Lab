-- Job logs, audit entries and job lists are looked up by job, state, app or
-- time; without these indexes each lookup read whole tables as history grew
-- (review R28).
CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id, id);
CREATE INDEX IF NOT EXISTS audit_job ON audit(job_id);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_service_created ON jobs(service_id, created_at);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS calls_created ON calls(created_at);
