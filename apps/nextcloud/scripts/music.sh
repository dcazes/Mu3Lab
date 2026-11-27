# Share Navidrome's music library as a Music folder everyone sees, or remove it (sh in the app container).
set -eu
occ() { runuser -u www-data -- php occ "$@"; }
# The id of the Music mount Mu3Lab made, if there is one.
existing() {
  occ files_external:list --output=json 2>/dev/null | php -r '
    $mounts = json_decode(stream_get_contents(STDIN), true) ?: [];
    foreach ($mounts as $m) {
      if (($m["configuration"]["datadir"] ?? "") === "/mnt/mu3lab-music") { echo $m["mount_id"]; break; }
    }'
}
if [ "${MU3LAB_MUSIC_SHARED:-0}" = 1 ]; then
  # Nextcloud writes as www-data; Navidrome only reads.
  chown -R www-data:www-data /mnt/mu3lab-music
  occ app:enable files_external >/dev/null || { echo "MU3LAB_ERROR Nextcloud could not enable external storage"; exit 3; }
  if [ -z "$(existing)" ]; then
    occ files_external:create /Music local null::null -c datadir=/mnt/mu3lab-music >/dev/null \
      || { echo "MU3LAB_ERROR Nextcloud could not add the Music folder"; exit 3; }
  fi
  # Look for changes on each visit (Nextcloud's own default for a folder added from the command line
  # is never), so songs Navidrome's library gains elsewhere show up.
  occ files_external:option "$(existing)" filesystem_check_changes 1 >/dev/null \
    || { echo "MU3LAB_ERROR Nextcloud could not keep the Music folder up to date"; exit 3; }
  # Let people share songs and albums from it (external storage allows no sharing by default).
  occ files_external:option "$(existing)" enable_sharing true >/dev/null \
    || { echo "MU3LAB_ERROR Nextcloud could not allow sharing from the Music folder"; exit 3; }
else
  if occ app:list --output=json 2>/dev/null | grep -q '"files_external"'; then
    id=$(existing)
    if [ -n "$id" ]; then
      occ files_external:delete --yes "$id" >/dev/null || { echo "MU3LAB_ERROR Nextcloud could not remove the Music folder"; exit 3; }
    fi
  fi
fi
echo MU3LAB_MUSIC_OK
