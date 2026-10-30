# Share every media library (audiobooks, games) as a folder everyone sees, and drop ones that are gone (sh in the app container).
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
if [ -n "$wanted" ]; then
  occ app:enable files_external >/dev/null || { echo "MU3LAB_ERROR Nextcloud could not enable external storage"; exit 3; }
fi
mounts=$(existing)
for name in $wanted; do
  dir="/mnt/mu3lab-media/$name"
  [ -d "$dir" ] || continue
  # Nextcloud writes as www-data; the media apps run as root and still read and write.
  chown -R www-data:www-data "$dir"
  if ! printf '%s\n' "$mounts" | grep -qx "[0-9]* $name"; then
    # audiobooks -> /Audiobooks; with no applicable users or groups, the folder is everyone's.
    title=$(printf '%s' "$name" | cut -c1 | tr a-z A-Z)$(printf '%s' "$name" | cut -c2-)
    occ files_external:create "/$title" local null::null -c "datadir=$dir" >/dev/null \
      || { echo "MU3LAB_ERROR Nextcloud could not add the $title folder"; exit 3; }
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
