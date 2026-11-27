# Share every media library (audiobooks, games) as a folder everyone sees, and drop ones that are gone (sh in the app container).
# A library in MU3LAB_MEDIA_ADMIN_FOLDERS (qBittorrent's add-torrents) is seen by Nextcloud's admin group only,
# which holds exactly Mu3Lab's administrators (configure.sh).
# The library stays the app's only copy: a file added in Nextcloud lands straight in Audiobookshelf's or RomM's folder.
set -eu
occ() { runuser -u www-data -- php occ "$@"; }
# "<mount id> <folder>" for each media mount Mu3Lab made.
existing() {
  occ files_external:list --output=json 2>/dev/null | php -r '
    $mounts = json_decode(stream_get_contents(STDIN), true) ?: [];
    foreach ($mounts as $m) {
      $dir = $m["configuration"]["datadir"] ?? "";
      if (strpos($dir, "/mnt/mu3lab-media/") === 0) { echo $m["mount_id"], " ", substr($dir, 18), "\n"; }
    }'
}
wanted=$(printf '%s' "${MU3LAB_MEDIA_FOLDERS:-}" | tr ',' '\n' | grep -E '^[a-z][a-z0-9-]*$' || true)
admins_only=$(printf '%s' "${MU3LAB_MEDIA_ADMIN_FOLDERS:-}" | tr ',' '\n' | grep -E '^[a-z][a-z0-9-]*$' || true)
if [ -n "$wanted" ]; then
  occ app:enable files_external >/dev/null || { echo "MU3LAB_ERROR Nextcloud could not enable external storage"; exit 3; }
fi
mounts=$(existing)
for name in $wanted; do
  dir="/mnt/mu3lab-media/$name"
  [ -d "$dir" ] || continue
  # Nextcloud writes as www-data; the media apps run as root and still read and write.
  chown -R www-data:www-data "$dir"
  # audiobooks -> /Audiobooks, add-torrents -> /Add torrents.
  title=$(printf '%s' "$name" | cut -c1 | tr a-z A-Z)$(printf '%s' "$name" | cut -c2- | tr '-' ' ')
  id=$(printf '%s\n' "$mounts" | awk -v n="$name" '$2 == n { print $1; exit }')
  if [ -z "$id" ]; then
    # With no applicable users or groups, the folder is everyone's.
    # It answers "Storage created with id 9".
    created=$(occ files_external:create "/$title" local null::null -c "datadir=$dir") \
      || { echo "MU3LAB_ERROR Nextcloud could not add the $title folder"; exit 3; }
    id=$(printf '%s' "$created" | grep -o '[0-9][0-9]*' | tail -n 1)
  fi
  # Look for changes on each visit. A folder added from the command line otherwise never does, and
  # files the apps add themselves (qBittorrent's downloads, filed episodes) would never appear.
  occ files_external:option "$id" filesystem_check_changes 1 >/dev/null \
    || { echo "MU3LAB_ERROR Nextcloud could not keep the $title folder up to date"; exit 3; }
  # Let people share files and folders from it like any of their own (Nextcloud's default for
  # external storage is no sharing at all).
  occ files_external:option "$id" enable_sharing true >/dev/null \
    || { echo "MU3LAB_ERROR Nextcloud could not allow sharing from the $title folder"; exit 3; }
  if printf '%s\n' "$admins_only" | grep -qx "$name"; then
    occ files_external:applicable --add-group=admin "$id" >/dev/null \
      || { echo "MU3LAB_ERROR Nextcloud could not keep the $title folder to administrators"; exit 3; }
  fi
done
# A library that is no longer there (someone removed it) leaves no empty folder behind.
printf '%s\n' "$mounts" | while read -r id name; do
  [ -n "$id" ] || continue
  if ! printf '%s\n' "$wanted" | grep -qx "$name"; then
    occ files_external:delete --yes "$id" >/dev/null || { echo "MU3LAB_ERROR Nextcloud could not remove the $name folder"; exit 3; }
  fi
done
echo MU3LAB_MEDIA_OK
