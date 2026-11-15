"""Mu3Lab's handling of finished torrents: file the video for Jellyfin, then stop sharing it.

qBittorrent runs this inside its own container each time a torrent finishes (start.py sets
that up). For every finished torrent:

1. It stops the torrent, so nothing is uploaded once the download is complete.
2. A torrent with no category still in the Downloads library is filed by what it holds:
   - mostly video, with a season in its name ("Show.Name.S02E05", "Season 2") -> Shows, in
     "Show Name/Season 02", the layout Jellyfin expects for series;
   - any other video -> Movies;
   - anything else stays in Downloads.
   qBittorrent moves the files itself (same drive, so it is instant).
3. It removes the torrent from qBittorrent, keeping its files.

Movies and Shows are shared folders in Nextcloud, and Jellyfin sees new files as they arrive.
It talks to qBittorrent on localhost, which needs no login (start.py).
"""

from __future__ import annotations

import fcntl
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

API = "http://localhost:8479/api/v2"
DOWNLOADS = "/media/downloads"
MOVIES = "/media/movies"
SHOWS = "/media/shows"
LOG = Path("/config/mu3lab-finished.log")
LOCK = Path("/config/mu3lab-finished.lock")
MOVE_SECONDS = 1800
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".flv"}
# The show's name, then its season: "S02", "S02E05", "Season 2" or "2x05".
SEASON = re.compile(
    r"^(?P<show>.*?)[\s._\-\[(]+"
    r"(?:s(?P<s>\d{1,2})(?=e\d|[^a-z0-9]|$)|season[\s._\-]?(?P<season>\d{1,2})(?!\d)|(?P<x>\d{1,2})x\d{2,3}(?!\d))",
    re.IGNORECASE,
)
TAGS = re.compile(r"^(?:\s*\[[^\]]*\])+")  # leading release-group tags such as "[Group]"
YEAR = re.compile(r"\s\(?((?:19|20)\d{2})\)?$")


def show_and_season(name: str) -> tuple[str, int] | None:
    """("Show Name (2005)", 2) from a release name, or None when it names no season."""
    match = SEASON.match(TAGS.sub("", PurePosixPath(name).name))
    if not match:
        return None
    show = re.sub(r"[._]+", " ", match["show"]).strip(" -[(")
    show = re.sub(r"\s+", " ", show.replace("/", " "))
    if not show:
        return None
    show = YEAR.sub(r" (\1)", show)
    return show, int(match["s"] or match["season"] or match["x"])


def _is_video_file(item: dict) -> bool:
    return PurePosixPath(str(item.get("name") or "")).suffix.lower() in VIDEO


def is_video(files: list[dict]) -> bool:
    """Half or more of the torrent's size is video (subtitles, posters and samples are small)."""
    total = sum(int(item.get("size") or 0) for item in files)
    video = sum(int(item.get("size") or 0) for item in files if _is_video_file(item))
    return total > 0 and video * 2 >= total


def destination(name: str, files: list[dict]) -> str | None:
    """The library folder for a finished video, or None to leave it in Downloads."""
    if not is_video(files):
        return None
    videos = sorted((item for item in files if _is_video_file(item)), key=lambda item: -int(item.get("size") or 0))
    found = show_and_season(name) or (show_and_season(str(videos[0]["name"])) if videos else None)
    if found:
        show, season = found
        return f"{SHOWS}/{show}/Season {season:02d}"
    return MOVIES


def call(path: str, data: dict[str, str] | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    with urllib.request.urlopen(urllib.request.Request(f"{API}/{path}", data=body), timeout=30) as response:
        text = response.read().decode()
    return json.loads(text) if text.strip().startswith(("[", "{")) else text


def log(message: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def info(hash_: str) -> dict:
    found = call(f"torrents/info?{urllib.parse.urlencode({'hashes': hash_})}")
    return found[0] if found else {}


def settled(hash_: str) -> dict:
    """The torrent once qBittorrent has finished moving its files ({} if gone or still moving)."""
    deadline = time.monotonic() + MOVE_SECONDS
    while time.monotonic() < deadline:
        now = info(hash_)
        if not now or now.get("state") != "moving":
            return now
        time.sleep(1)
    return {}


def finish(torrent: dict, libraries: bool) -> None:
    hash_ = str(torrent.get("hash") or "")
    name = str(torrent.get("name") or "")
    call("torrents/stop", {"hashes": hash_})  # no uploading once it is complete
    # Freshly finished files may still be on their way out of Downloads/.incomplete.
    torrent = settled(hash_)
    if not torrent:
        log(f"{name}: still moving; it stays stopped and is finished off next time")
        return
    if libraries and not torrent.get("category") and str(torrent.get("save_path") or "").rstrip("/") == DOWNLOADS:
        folder = destination(name, call(f"torrents/files?{urllib.parse.urlencode({'hash': hash_})}"))
        if folder:
            call("torrents/setLocation", {"hashes": hash_, "location": folder})
            if str(settled(hash_).get("save_path") or "").rstrip("/") != folder:
                log(f"{name}: not yet moved to {folder}; it stays stopped and is finished off next time")
                return
            log(f"{name} -> {folder}")
    call("torrents/delete", {"hashes": hash_, "deleteFiles": "false"})
    log(f"{name}: finished; removed from qBittorrent, files kept")


def main() -> int:
    # One run at a time: two torrents finishing together start two runs.
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Movies and Shows exist once Jellyfin is installed; until then videos stay in Downloads.
        libraries = Path(MOVIES).is_dir() and Path(SHOWS).is_dir()
        try:
            torrents = call("torrents/info?filter=completed")
        except OSError as error:
            log(f"qBittorrent did not answer: {error}")
            return 1
        for torrent in torrents:
            try:
                finish(torrent, libraries)
            except (OSError, ValueError, KeyError) as error:
                log(f"{torrent.get('name')}: not finished off ({error})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
