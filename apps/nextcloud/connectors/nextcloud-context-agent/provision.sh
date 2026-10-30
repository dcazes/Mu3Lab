# Prints a fresh Nextcloud app password for one person, by username (sh, on stdin; the username is $1).
set -eu
occ() { runuser -u www-data -- php occ "$@"; }
user=${1:-}
case "$user" in "" | -* | *[!A-Za-z0-9._@-]*) echo "MU3LAB_ERROR the person's username is missing or unusable"; exit 3 ;; esac
if ! occ user:info "$user" >/dev/null 2>&1; then
  echo "MU3LAB_ERROR Open Nextcloud and sign in once; chat connects after your Nextcloud account exists"
  exit 3
fi
for id in $(occ user:auth-tokens:list "$user" --output=json | php -r 'foreach (json_decode(stream_get_contents(STDIN), true) ?: [] as $t) { if (($t["name"] ?? "") === "Mu3Lab MCP") echo $t["id"], "\n"; }'); do
  occ user:auth-tokens:delete --no-interaction "$user" "$id" >/dev/null
done
password=$(occ user:auth-tokens:add --no-interaction --name="Mu3Lab MCP" "$user" | tail -n 1 | tr -d '[:space:]')
case "$password" in
  *[!A-Za-z0-9]* | "") echo "MU3LAB_ERROR Nextcloud did not return an app password"; exit 3 ;;
esac
echo "MU3LAB_OUTPUT username=$user"
echo "MU3LAB_OUTPUT app_password=$password"
