-- The backup simplification (2026-10-11) replaced the operation journal and
-- Mu3Lab's own backup scheduler: restores and updates now leave one
-- in-progress note, and Kopia's server keeps the schedule.
DROP TABLE IF EXISTS operation_steps;
DROP TABLE IF EXISTS operations;
DELETE FROM records WHERE scope = 'backup' AND name IN ('schedule-run', 'verification');
