# Succeed when Nextcloud has the expected administrator ($1).
set -eu
runuser -u www-data -- php occ user:list --output=json \
  | php -r '$u = json_decode(stream_get_contents(STDIN), true) ?: []; exit(array_key_exists($argv[1], $u) ? 0 : 1);' "$1" \
  || { echo "MU3LAB_ERROR the administrator account was not created"; exit 3; }
