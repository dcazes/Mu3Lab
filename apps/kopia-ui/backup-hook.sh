#!/bin/sh
# Kopia runs this just before and just after it backs up each source
# (see ctl/backup_service.py). Mu3Lab stops the app before its backup and
# starts it again after. A failure before the backup makes Kopia skip it.
exec curl --silent --show-error --fail --max-time 1700 \
  -X POST \
  -H "Authorization: Bearer ${MU3LAB_BACKUP_HOOK_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"source\": \"${KOPIA_SOURCE_PATH}\"}" \
  "http://127.0.0.1:8787/api/v1/backup-hooks/${KOPIA_ACTION}"
