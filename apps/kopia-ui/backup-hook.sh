#!/bin/sh
# Kopia runs this just before and just after it backs up each source
# (see ctl/backup_service.py). Before, Mu3Lab writes a consistent copy of the
# app's databases while the app keeps running; a failure makes Kopia skip that
# backup. --max-time sits between Mu3Lab's wait (2 h 50 min) and Kopia's 3 h.
exec curl --silent --show-error --fail --max-time 10500 \
  -X POST \
  -H "Authorization: Bearer ${MU3LAB_BACKUP_HOOK_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"source\": \"${KOPIA_SOURCE_PATH}\"}" \
  "http://127.0.0.1:8787/api/v1/backup-hooks/${KOPIA_ACTION}"
