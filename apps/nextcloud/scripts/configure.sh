# Enable Calendar and user_oidc, register Authentik, and make it the only sign-in (sh in the app container).
set -eu
occ() { runuser -u www-data -- php occ "$@"; }
if [ -z "${MU3LAB_OIDC_CLIENT_ID:-}" ] || [ -z "${MU3LAB_OIDC_CLIENT_SECRET:-}" ] || [ -z "${MU3LAB_OIDC_DISCOVERY_URL:-}" ]; then
  echo "MU3LAB_ERROR Nextcloud's Authentik settings are incomplete"
  exit 3
fi
# app:install resolves the newest release compatible with this server.
for app in calendar user_oidc; do
  occ app:install "$app" >/dev/null 2>&1 || true
  occ app:enable "$app" >/dev/null || { echo "MU3LAB_ERROR Nextcloud could not enable $app"; exit 3; }
done
occ app:disable firstrunwizard >/dev/null 2>&1 || true
occ user_oidc:provider mu3lab --clientid="$MU3LAB_OIDC_CLIENT_ID" --clientsecret="$MU3LAB_OIDC_CLIENT_SECRET" \
  --discoveryuri="$MU3LAB_OIDC_DISCOVERY_URL" --mapping-uid=preferred_username --unique-uid=0 >/dev/null \
  || { echo "MU3LAB_ERROR Nextcloud could not register Authentik"; exit 3; }
id=$(occ user_oidc:providers --output=json | php -r '
  foreach (preg_split("/\r?\n/", stream_get_contents(STDIN)) as $line) {
    $p = json_decode($line, true);
    if (is_array($p) && ($p["identifier"] ?? "") === "mu3lab") { echo $p["id"]; }
  }')
case "$id" in ''|*[!0-9]*) echo "MU3LAB_ERROR Nextcloud did not report its Authentik provider"; exit 3 ;; esac
occ config:system:set user_oidc auto_provision --type=boolean --value=true >/dev/null
occ config:system:set user_oidc soft_auto_provision --type=boolean --value=true >/dev/null
# One login backend: Nextcloud sends people straight to Authentik.
occ config:app:set user_oidc allow_multiple_user_backends --value=0 >/dev/null
# The tailnet name resolves to a CGNAT address, which Nextcloud otherwise refuses during discovery.
occ config:system:set allow_local_remote_servers --type=boolean --value=true >/dev/null
echo "MU3LAB_OUTPUT NEXTCLOUD_OIDC_PROVIDER_ID=$id"
