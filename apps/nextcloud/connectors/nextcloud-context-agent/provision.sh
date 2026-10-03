# Prints a fresh Nextcloud app password for the one administrator (sh, on stdin).
set -eu
occ() { runuser -u www-data -- php occ "$@"; }
admins=$(occ group:list --output=json | php -r '$g = json_decode(stream_get_contents(STDIN), true); echo implode("\n", $g["admin"] ?? []);')
count=$(printf '%s\n' "$admins" | grep -c . || true)
if [ "$count" -ne 1 ]; then
  echo "MU3LAB_ERROR Nextcloud needs exactly one administrator for automatic chat setup"
  exit 3
fi
user=$admins
for id in $(occ user:auth-tokens:list "$user" --output=json | php -r 'foreach (json_decode(stream_get_contents(STDIN), true) ?: [] as $t) { if (($t["name"] ?? "") === "Mu3Lab MCP") echo $t["id"], "\n"; }'); do
  occ user:auth-tokens:delete --no-interaction "$user" "$id" >/dev/null
done
password=$(occ user:auth-tokens:add --no-interaction --name="Mu3Lab MCP" "$user" | tail -n 1 | tr -d '[:space:]')
case "$password" in
  *[!A-Za-z0-9]* | "") echo "MU3LAB_ERROR Nextcloud did not return an app password"; exit 3 ;;
esac
echo "MU3LAB_OUTPUT username=$user"
echo "MU3LAB_OUTPUT app_password=$password"
