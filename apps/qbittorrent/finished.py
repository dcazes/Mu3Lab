"""Mu3Lab's handling of finished torrents: file the video for Jellyfin, then stop sharing it.

qBittorrent runs this inside its own container each time a torrent finishes (start.py sets
that up). It looks after torrents people add by hand (a magnet link, the Add torrents folder,
chat) and the library categories Mu3Lab makes. A torrent in any other category belongs to the
program that added it, such as Sonarr or Radarr: it is only stopped, and that program files it
and removes it. For every finished torrent:

1. It stops the torrent, so nothing is uploaded once the download is complete.
2. A torrent with no category still in the Downloads library is filed by what it holds
   (one given the shows category goes into its show's season folder the same way):
   - mostly video, with a season in its name ("Show.Name.S02E05", "Season 2") -> Shows, in
     "Show Name/Season 02", the layout Jellyfin expects for series;
   - any other video -> Movies;
   - anything else stays in Downloads.
   qBittorrent moves the files itself (same drive, so it is instant).
3. It removes the torrent from qBittorrent, keeping its files.
4. A filed torrent that came as a folder (video plus release extras) is tidied: the video and
   any subtitles move up beside the others, and extras such as .nfo, .txt adverts and sample
   clips go. Jellyfin notices a file arriving more reliably than a folder, and Nextcloud shows
   one file per episode.

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
# Mu3Lab's categories, one per library (start.py), and none at all: the torrents finished off here.
OWN_CATEGORIES = {"", "music", "audiobooks", "podcasts", "roms", "movies", "shows"}
LOG = Path("/config/mu3lab-finished.log")
LOCK = Path("/config/mu3lab-finished.lock")
MOVE_SECONDS = 1800
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts", ".m2ts", ".mpg", ".mpeg", ".flv"}
SUBTITLES = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}
# What release groups add beside the video; anything else stays, and so does its folder.
EXTRAS = {".nfo", ".txt", ".jpg", ".jpeg", ".png", ".url", ".lnk", ".exe", ".sfv", ".md5", ".nzb", ".htm", ".html"}
SAMPLE = re.compile(r"(^|[\s._\-])sample([\s._\-]|$)", re.IGNORECASE)
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
        return f"{SHOWS}/{show_folder(show)}/Season {season:02d}"
    return MOVIES


def show_folder(show: str) -> str:
    """The show's folder: one already in Shows whatever its capitals, else the name as released
    (capitalised when the release is all lower case), so one show never gets two folders."""
    try:
        existing = [item.name for item in Path(SHOWS).iterdir() if item.is_dir()]
    except OSError:
        existing = []
    same = next((folder for folder in existing if folder.casefold() == show.casefold()), None)
    if same:
        return same
    return show.title() if show == show.lower() else show


def tidy(root: Path, folder: Path) -> None:
    """Move a filed folder torrent's video and subtitles up into ``folder``; drop the extras."""
    if not root.is_dir() or root.parent != folder:
        return  # a single file is already where it belongs
    for item in sorted(path for path in root.rglob("*") if path.is_file()):
        suffix = item.suffix.lower()
        sample = SAMPLE.search(item.stem) is not None
        if (suffix in VIDEO and not sample) or suffix in SUBTITLES:
            target = folder / item.name
            if not target.exists():
                item.rename(target)
        elif suffix in EXTRAS or (suffix in VIDEO and sample):
            item.unlink()
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda path: -len(path.parts)):
        if not any(directory.iterdir()):
            directory.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


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
    category = str(torrent.get("category") or "")
    if category not in OWN_CATEGORIES:
        return  # Sonarr's, Radarr's or another program's: it files the files and removes the torrent
    saved = str(torrent.get("save_path") or "").rstrip("/")
    content = ""
    # No category: file it by what it holds. The shows category: its category folder is the
    # top of Shows, so put the episode in its show's season folder, as Jellyfin expects.
    if libraries and ((not category and saved == DOWNLOADS) or (category == "shows" and saved == SHOWS)):
        folder = destination(name, call(f"torrents/files?{urllib.parse.urlencode({'hash': hash_})}"))
        if category == "shows" and folder == MOVIES:
            folder = None  # named without a season: left where it was saved
        if folder:
            call("torrents/setLocation", {"hashes": hash_, "location": folder})
            moved = settled(hash_)
            if str(moved.get("save_path") or "").rstrip("/") != folder:
                log(f"{name}: not yet moved to {folder}; it stays stopped and is finished off next time")
                return
            log(f"{name} -> {folder}")
            content = str(moved.get("content_path") or "")
    call("torrents/delete", {"hashes": hash_, "deleteFiles": "false"})
    log(f"{name}: finished; removed from qBittorrent, files kept")
    if content:
        try:
            tidy(Path(content), Path(folder))
        except OSError as error:
            log(f"{name}: left as a folder ({error})")


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
