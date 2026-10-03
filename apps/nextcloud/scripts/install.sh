# Finish Nextcloud's base installation on first start (sh, run as root in the app container).
#
# The image's entrypoint copies Nextcloud into its volume and, given the
# first-start NEXTCLOUD_ADMIN_* settings, installs it itself while holding a
# lock on this file. Installing again while it does creates the tables under
# two database roles, so wait for the lock, and install only if it did not.
set -eu
occ() { runuser -u www-data -- php occ "$@"; }
installed() {
  occ status --output=json 2>/dev/null | php -r '$s = json_decode(stream_get_contents(STDIN), true); exit(empty($s["installed"]) ? 1 : 0);'
}
lock=/var/www/html/nextcloud-init-sync.lock
deadline=$(( $(date +%s) + 600 ))
until { [ ! -e "$lock" ] || flock -n "$lock" true; } && occ status --output=json >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "MU3LAB_ERROR Nextcloud did not become ready for its first-run installation"
    exit 3
  fi
  sleep 3
done
installed && exit 0
if [ -z "${NEXTCLOUD_ADMIN_USER:-}" ] || [ -z "${NEXTCLOUD_ADMIN_PASSWORD:-}" ]; then
  echo "MU3LAB_ERROR Nextcloud is not installed and has no first-start administrator"
  exit 3
fi
occ maintenance:install --database pgsql --database-host db --database-name nextcloud \
  --database-user nextcloud --database-pass="$POSTGRES_PASSWORD" \
  --admin-user="$NEXTCLOUD_ADMIN_USER" --admin-pass="$NEXTCLOUD_ADMIN_PASSWORD" >/dev/null 2>&1 || true
installed || { echo "MU3LAB_ERROR Nextcloud did not confirm a completed base installation"; exit 3; }
