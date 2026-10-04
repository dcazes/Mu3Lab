# Prints a new Immich API key for the one active administrator (sh in the database container, on stdin).
set -eu
q() { psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"; }
admins=$(q "select id from \"user\" where \"isAdmin\" is true and \"deletedAt\" is null and status='active' limit 2;")
count=$(printf '%s\n' "$admins" | grep -c . || true)
if [ "$count" -eq 0 ]; then
  echo "MU3LAB_ERROR Open Immich and sign in once; chat connects after your Immich account exists"
  exit 3
fi
if [ "$count" -ne 1 ]; then
  echo "MU3LAB_ERROR Immich needs exactly one active administrator for automatic chat setup"
  exit 3
fi
case "$admins" in *[!0-9a-f-]*) echo "MU3LAB_ERROR unexpected Immich user id"; exit 3 ;; esac
token=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 43)
digest=$(printf '%s' "$token" | sha256sum | cut -d' ' -f1)
q "BEGIN;
DELETE FROM api_key WHERE \"userId\" = '$admins'::uuid AND name = 'Mu3Lab MCP';
INSERT INTO api_key (id, name, key, \"userId\", permissions, \"createdAt\", \"updatedAt\", \"updateId\")
VALUES (gen_random_uuid(), 'Mu3Lab MCP', decode('$digest', 'hex'), '$admins'::uuid, ARRAY['all']::varchar[], now(), now(), gen_random_uuid());
COMMIT;" >/dev/null
echo "MU3LAB_OUTPUT api_key=$token"
