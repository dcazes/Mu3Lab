# Prints a new Immich API key for one person, by email (sh in the database container, on stdin; the email is $1).
set -eu
q() { psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"; }
[ -n "${1:-}" ] || { echo "MU3LAB_ERROR the person's email is missing"; exit 3; }
admins=$(printf '%s\n' "select id from \"user\" where lower(email) = lower(:'email') and \"deletedAt\" is null and status='active';" \
  | psql -v ON_ERROR_STOP=1 -v email="$1" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At)
if [ -z "$admins" ]; then
  echo "MU3LAB_ERROR Open Immich and sign in once; chat connects after your Immich account exists"
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
