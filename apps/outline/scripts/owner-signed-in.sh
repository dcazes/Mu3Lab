# Read-only: has the installing owner's first sign-in created Outline's workspace?
# Outline makes the first person to sign in its administrator, and until then
# Authentik admits only the owner, so any active administrator is the owner.
set -eu
found=$(psql -U outline -d outline -tAc \
  "SELECT 1 FROM users WHERE role = 'admin' AND \"deletedAt\" IS NULL AND \"suspendedAt\" IS NULL LIMIT 1")
if [ "$found" = "1" ]; then
  echo MU3LAB_OUTLINE_OWNER_OK
fi
