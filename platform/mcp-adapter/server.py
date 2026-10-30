"""Small, fixed-surface Streamable HTTP MCP adapters for app data only."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

MODE = os.environ.get("MCP_APP", "")
APP_URL = os.environ.get("APP_URL", "").rstrip("/")
MAX_INPUT = 65_536
MAX_OUTPUT = 250_000


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def tool(name: str, description: str, parameters: dict) -> dict:
    return {"name": name, "description": description, "inputSchema": parameters}


TEXT = {"type": "string", "minLength": 1}
if MODE == "nextcloud":
    TOOLS = [
        tool("list_files", "List one Nextcloud folder", schema({"path": {"type": "string", "default": ""}})),
        tool("read_text_file", "Read one UTF-8 text file from Nextcloud", schema({"path": TEXT}, ["path"])),
        tool(
            "write_text_file",
            "Create or replace one UTF-8 text file in Nextcloud",
            schema({"path": TEXT, "content": {"type": "string", "maxLength": 65536}}, ["path", "content"]),
        ),
    ]
elif MODE == "adventurelog":
    TOOLS = [
        tool("list_collections", "List AdventureLog collections", schema({})),
        tool("list_locations", "List AdventureLog locations", schema({})),
        tool(
            "create_collection",
            "Create one AdventureLog collection",
            schema({"name": TEXT, "description": {"type": "string"}}, ["name"]),
        ),
    ]
elif MODE == "beaver":
    DAY = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "A date as YYYY-MM-DD"}
    TOOLS = [
        tool("list_habits", "List the owner's active habits with their IDs", schema({})),
        tool(
            "list_completions",
            "List the days one habit was done, newest first",
            schema(
                {
                    "habit_id": TEXT,
                    "date_start": DAY,
                    "date_end": DAY,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 366, "default": 30},
                },
                ["habit_id"],
            ),
        ),
        tool(
            "mark_habit",
            "Mark one habit as done, or not done, on one day",
            schema({"habit_id": TEXT, "date": DAY, "done": {"type": "boolean"}}, ["habit_id", "date", "done"]),
        ),
    ]
elif MODE == "audiobookshelf":
    LIMIT = {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}
    TOOLS = [
        tool("list_libraries", "List the Audiobookshelf libraries (audiobooks, podcasts) with their IDs", schema({})),
        tool(
            "search_library",
            "Search one library for books, podcasts, authors and series",
            schema({"library_id": TEXT, "query": TEXT, "limit": LIMIT}, ["library_id", "query"]),
        ),
        tool(
            "list_recent_items",
            "List the items most recently added to one library",
            schema({"library_id": TEXT, "limit": LIMIT}, ["library_id"]),
        ),
        tool("list_in_progress", "List the books and podcasts the owner is part-way through", schema({})),
        tool("listening_stats", "The owner's listening time: total, today, and the most listened items", schema({})),
    ]
elif MODE == "navidrome":
    LIMIT = {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
    TOOLS = [
        tool(
            "search_music",
            "Search the music library for artists, albums and songs",
            schema({"query": TEXT, "limit": LIMIT}, ["query"]),
        ),
        tool(
            "list_albums",
            "List albums: newest added, recently played, most played, random, alphabetical or starred",
            schema(
                {
                    "order": {
                        "type": "string",
                        "enum": ["newest", "recent", "frequent", "random", "alphabeticalByName", "starred"],
                        "default": "newest",
                    },
                    "limit": LIMIT,
                }
            ),
        ),
        tool("get_album", "List one album's songs", schema({"album_id": TEXT}, ["album_id"])),
        tool("list_playlists", "List the playlists Mu3Lab's assistant can see (its own and public ones)", schema({})),
        tool("get_playlist", "List one playlist's songs", schema({"playlist_id": TEXT}, ["playlist_id"])),
        tool(
            "create_playlist",
            "Create a playlist from song IDs, shared with everyone in the household",
            schema(
                {"name": TEXT, "song_ids": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 500}},
                ["name", "song_ids"],
            ),
        ),
    ]
elif MODE == "authentik":
    EVENT_ACTIONS = ["login", "login_failed", "logout", "user_write", "password_set", "suspicious_request"]
    TOOLS = [
        tool("list_people", "List everyone who can sign in: name, email, groups, admin, last sign-in", schema({})),
        tool("list_groups", "List the sign-in groups and how many people are in each", schema({})),
        tool(
            "recent_events",
            "List recent sign-in and account events, newest first, optionally of one kind",
            schema(
                {
                    "action": {"type": "string", "enum": EVENT_ACTIONS},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                }
            ),
        ),
    ]
elif MODE == "kopia":
    TOOLS = [
        tool(
            "backup_status",
            "Each app's backup: status, when the last backup ran, its size and errors, and when the next is due",
            schema({}),
        ),
        tool("repository_status", "Whether Kopia is connected to the backup storage, and which storage", schema({})),
    ]
elif MODE == "romm":
    TOOLS = [
        tool("list_platforms", "List the game systems in the RomM library with how many games each has", schema({})),
        tool(
            "search_games",
            "Search the game library by name, optionally on one system",
            schema(
                {
                    "query": TEXT,
                    "platform_id": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                ["query"],
            ),
        ),
        tool(
            "get_game",
            "Details of one game: system, summary, genres, release date",
            schema({"game_id": {"type": "integer", "minimum": 1}}, ["game_id"]),
        ),
        tool("list_collections", "List the RomM collections", schema({})),
    ]
elif MODE == "calendar":
    WHEN = {"type": "string", "description": "ISO 8601 date-time, or YYYY-MM-DD for an all-day event"}
    TOOLS = [
        tool(
            "list_events",
            "List the person's calendar events between two times (default: the next 30 days), soonest first",
            schema(
                {
                    "start": WHEN,
                    "end": WHEN,
                    "query": {"type": "string", "description": "Only events whose title contains this text"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 50},
                }
            ),
        ),
        tool(
            "create_event",
            "Add an event to the person's calendar",
            schema(
                {"title": TEXT, "start": WHEN, "end": WHEN, "all_day": {"type": "boolean", "default": False}},
                ["title", "start", "end"],
            ),
        ),
        tool(
            "update_event",
            "Change one event: its title, times or all-day flag. Pass the revision list_events returned.",
            schema(
                {
                    "event_id": TEXT,
                    "title": TEXT,
                    "start": WHEN,
                    "end": WHEN,
                    "all_day": {"type": "boolean", "default": False},
                    "revision": {"type": "string", "default": ""},
                },
                ["event_id", "title", "start", "end"],
            ),
        ),
        tool(
            "delete_event",
            "Delete one event. Pass the revision list_events returned.",
            schema({"event_id": TEXT, "revision": {"type": "string", "default": ""}}, ["event_id"]),
        ),
    ]
else:
    raise SystemExit("unsupported adapter mode")

# The calendar adapter acts for whoever calls it: each call's own key, checked by Mu3Lab.
CALLER = __import__("threading").local()


BEARER = {
    "beaver": ("BEAVER_API_TOKEN", "Beaver Habits API token"),
    "audiobookshelf": ("ABS_API_KEY", "Audiobookshelf API key"),
    "romm": ("ROMM_API_TOKEN", "RomM API token"),
    "authentik": ("AUTHENTIK_API_TOKEN", "Authentik read-only token"),
}


def _auth() -> str:
    if MODE == "calendar":
        return getattr(CALLER, "authorization", "")
    if MODE == "nextcloud":
        username = os.environ.get("NEXTCLOUD_USERNAME", "")
        password = os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
        if not username or not password:
            raise ValueError("Nextcloud app credential is not configured")
        return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    if MODE in BEARER:
        env, label = BEARER[MODE]
        token = os.environ.get(env, "")
        if not token:
            raise ValueError(f"{label} is not configured")
        return "Bearer " + token
    if MODE == "navidrome":
        return ""  # the Subsonic API signs each request in its query (_subsonic)
    if MODE == "kopia":
        username = os.environ.get("KOPIA_USERNAME", "")
        password = os.environ.get("KOPIA_PASSWORD", "")
        if not username or not password:
            raise ValueError("Kopia's control login is not configured")
        return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    token = os.environ.get("ADVENTURELOG_API_KEY", "")
    if not token:
        raise ValueError("AdventureLog API key is not configured")
    # AdventureLog reads "Api-Key"; it treats a Bearer header as an anonymous request.
    return "Api-Key " + token


def _path(value: str) -> str:
    if (
        not isinstance(value, str)
        or value.startswith("/")
        or "\\" in value
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ValueError("path must stay within the account's files")
    return "/".join(quote(part, safe="") for part in value.split("/") if part)


def _request(
    path: str, method: str = "GET", payload: bytes | None = None, content_type: str = "application/json"
) -> tuple[bytes, int]:
    headers = {"Accept": "application/xml" if method == "PROPFIND" else "application/json"}
    auth = _auth()
    if auth:
        headers["Authorization"] = auth
    if MODE == "nextcloud":
        headers["OCS-APIRequest"] = "true"
    if payload is not None:
        headers["Content-Type"] = content_type
    req = Request(APP_URL + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read(MAX_OUTPUT + 1)
            if len(data) > MAX_OUTPUT:
                raise ValueError("app response exceeded the size limit")
            return data, response.status
    except HTTPError as exc:
        if MODE == "calendar":
            # Mu3Lab's own explanation (a conflicting change, a missing event) helps the person act.
            try:
                reason = json.loads(exc.read(4096) or b"{}")
            except (ValueError, OSError):
                reason = {}
            message = reason.get("message") or reason.get("detail") if isinstance(reason, dict) else ""
            if isinstance(message, str) and message:
                raise ValueError(message[:300]) from None
        raise ValueError(f"app returned HTTP {exc.code}") from None
    except URLError:
        raise ValueError("app is unavailable") from None


def _nextcloud(name: str, args: dict):
    user = quote(os.environ.get("NEXTCLOUD_USERNAME", ""), safe="")
    path = _path(args.get("path", ""))
    route = f"/remote.php/dav/files/{user}/" + path
    if name == "list_files":
        data, _ = _request(route, "PROPFIND", b"", "application/xml")
        root = ElementTree.fromstring(data)
        return [{"href": item.text} for item in root.findall(".//{DAV:}href") if item.text]
    if not path:
        raise ValueError("a file path is required")
    if name == "read_text_file":
        data, _ = _request(route)
        return {"content": data.decode("utf-8")}
    if name == "write_text_file":
        content = args.get("content", "")
        if not isinstance(content, str) or len(content.encode()) > MAX_INPUT:
            raise ValueError("content must be text under 64 KB")
        _, status = _request(route, "PUT", content.encode(), "text/plain; charset=utf-8")
        return {"saved": True, "status": status}
    raise ValueError("unknown tool")


def _adventurelog(name: str, args: dict):
    if name in {"list_collections", "list_locations"}:
        path = "/api/collections/" if name == "list_collections" else "/api/locations/"
        data, _ = _request(path)
        return json.loads(data)
    if name == "create_collection":
        value = {"name": args.get("name"), "description": args.get("description", "")}
        if not isinstance(value["name"], str) or not value["name"].strip():
            raise ValueError("name is required")
        data, _ = _request("/api/collections/", "POST", json.dumps(value).encode())
        return json.loads(data)
    raise ValueError("unknown tool")


def _beaver(name: str, args: dict):
    if name == "list_habits":
        data, _ = _request("/api/v1/habits")
        return json.loads(data)
    habit_id = args.get("habit_id")
    if not isinstance(habit_id, str) or not habit_id.strip():
        raise ValueError("habit_id is required")
    route = f"/api/v1/habits/{quote(habit_id, safe='')}/completions"
    if name == "list_completions":
        limit = args.get("limit", 30)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 366:
            raise ValueError("limit must be a whole number from 1 to 366")
        query = {"date_fmt": "%Y-%m-%d", "sort": "desc", "limit": limit}
        query |= {key: args[key] for key in ("date_start", "date_end") if args.get(key)}
        data, _ = _request(route + "?" + urlencode(query))
        return json.loads(data)
    if name == "mark_habit":
        if not isinstance(args.get("done"), bool) or not isinstance(args.get("date"), str):
            raise ValueError("date and done are required")
        value = {"date": args["date"], "date_fmt": "%Y-%m-%d", "done": args["done"]}
        data, _ = _request(route, "POST", json.dumps(value).encode())
        return json.loads(data)
    raise ValueError("unknown tool")


def _get(path: str, query: dict | None = None):
    data, _ = _request(path + ("?" + urlencode(query, doseq=True) if query else ""))
    return json.loads(data)


def _text(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _id(args: dict, key: str) -> str:
    return quote(_text(args, key), safe="")


def _dicts(value) -> list[dict]:
    """The objects in a list from an app's reply, skipping anything else."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _limit(args: dict, default: int) -> int:
    value = args.get("limit", default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 50:
        raise ValueError("limit must be a whole number from 1 to 50")
    return value


def _short(text, size: int = 400):
    return text[:size] if isinstance(text, str) else text


def _book(item: dict) -> dict:
    """One Audiobookshelf library item, without covers, files and chapters."""
    media = item.get("media") or {}
    meta = media.get("metadata") or {}
    return {
        "id": item.get("id"),
        "type": item.get("mediaType"),
        "title": meta.get("title"),
        "author": meta.get("authorName") or [a.get("name") for a in _dicts(meta.get("authors"))],
        "narrator": meta.get("narratorName"),
        "series": meta.get("seriesName"),
        "published": meta.get("publishedYear"),
        "genres": meta.get("genres"),
        "description": _short(meta.get("description")),
        "duration_seconds": media.get("duration"),
    }


def _audiobookshelf(name: str, args: dict):
    if name == "list_libraries":
        libraries = _dicts(_get("/api/libraries").get("libraries"))
        return [{"id": lib.get("id"), "name": lib.get("name"), "type": lib.get("mediaType")} for lib in libraries]
    if name == "list_in_progress":
        items = _dicts(_get("/api/me/items-in-progress").get("libraryItems"))
        return [_book(item) | {"last_listened": item.get("progressLastUpdate")} for item in items]
    if name == "listening_stats":
        stats = _get("/api/me/listening-stats")
        items = sorted(
            _dicts(list((stats.get("items") or {}).values())),
            key=lambda item: item.get("timeListening") or 0,
            reverse=True,
        )
        return {
            "total_seconds": stats.get("totalTime"),
            "today_seconds": stats.get("today"),
            "most_listened": [
                {
                    "title": (item.get("mediaMetadata") or {}).get("title"),
                    "seconds": item.get("timeListening"),
                }
                for item in items[:10]
            ],
        }
    library = "/api/libraries/" + _id(args, "library_id")
    if name == "search_library":
        found = _get(library + "/search", {"q": _text(args, "query"), "limit": _limit(args, 10)})
        return {
            "books": [_book(match.get("libraryItem") or {}) for match in _dicts(found.get("book"))],
            "podcasts": [_book(match.get("libraryItem") or {}) for match in _dicts(found.get("podcast"))],
            "authors": [author.get("name") for author in _dicts(found.get("authors"))],
            "series": [(match.get("series") or {}).get("name") for match in _dicts(found.get("series"))],
        }
    if name == "list_recent_items":
        found = _get(library + "/items", {"sort": "addedAt", "desc": 1, "limit": _limit(args, 10)})
        return [_book(item) for item in _dicts(found.get("results"))]
    raise ValueError("unknown tool")


def _subsonic(method: str, query: dict | None = None) -> dict:
    """One Subsonic API call as Mu3Lab's Navidrome account, signed with a salted token."""
    password = os.environ.get("NAVIDROME_PASSWORD", "")
    if not password:
        raise ValueError("Navidrome password is not configured")
    salt = secrets.token_hex(8)
    # MD5 of password + salt is the Subsonic protocol's own scheme; the password itself never travels.
    signed = {
        "u": os.environ.get("NAVIDROME_USERNAME", ""),
        "t": hashlib.md5((password + salt).encode()).hexdigest(),
        "s": salt,
        "v": "1.16.1",
        "c": "mu3lab-mcp",
        "f": "json",
    }
    reply = _get(f"/rest/{method}", signed | (query or {})).get("subsonic-response") or {}
    if reply.get("status") != "ok":
        raise ValueError("Navidrome refused: " + str((reply.get("error") or {}).get("message") or "unknown error"))
    return reply


def _album(album: dict) -> dict:
    keys = ("id", "name", "artist", "year", "genre", "songCount", "playCount", "starred")
    return {key: album[key] for key in keys if key in album}


def _song(song: dict) -> dict:
    keys = ("id", "title", "artist", "album", "track", "year", "duration", "playCount")
    return {key: song[key] for key in keys if key in song}


def _playlist(playlist: dict) -> dict:
    keys = ("id", "name", "owner", "public", "songCount", "duration", "comment")
    return {key: playlist[key] for key in keys if key in playlist}


def _navidrome(name: str, args: dict):
    if name == "search_music":
        size = _limit(args, 20)
        query = {"query": _text(args, "query"), "artistCount": size, "albumCount": size, "songCount": size}
        found = _subsonic("search3", query).get("searchResult3") or {}
        return {
            "artists": [{"id": a.get("id"), "name": a.get("name")} for a in _dicts(found.get("artist"))],
            "albums": [_album(album) for album in _dicts(found.get("album"))],
            "songs": [_song(song) for song in _dicts(found.get("song"))],
        }
    if name == "list_albums":
        order = args.get("order", "newest")
        if order not in {"newest", "recent", "frequent", "random", "alphabeticalByName", "starred"}:
            raise ValueError("unknown album order")
        found = _subsonic("getAlbumList2", {"type": order, "size": _limit(args, 20)}).get("albumList2") or {}
        return [_album(album) for album in _dicts(found.get("album"))]
    if name == "get_album":
        album = _subsonic("getAlbum", {"id": _text(args, "album_id")}).get("album") or {}
        return _album(album) | {"songs": [_song(song) for song in _dicts(album.get("song"))]}
    if name == "list_playlists":
        found = _subsonic("getPlaylists").get("playlists") or {}
        return [_playlist(playlist) for playlist in _dicts(found.get("playlist"))]
    if name == "get_playlist":
        playlist = _subsonic("getPlaylist", {"id": _text(args, "playlist_id")}).get("playlist") or {}
        return _playlist(playlist) | {"songs": [_song(song) for song in _dicts(playlist.get("entry"))]}
    if name == "create_playlist":
        title, songs = _text(args, "name"), args.get("song_ids")
        if not isinstance(songs, list) or not songs or not all(isinstance(song, str) and song for song in songs):
            raise ValueError("song_ids must be a list of song IDs")
        playlist = _subsonic("createPlaylist", {"name": title, "songId": songs[:500]}).get("playlist") or {}
        if playlist.get("id"):
            # Playlists belong to Mu3Lab's own account; public ones show up for everyone.
            _subsonic("updatePlaylist", {"playlistId": playlist["id"], "public": "true"})
            playlist["public"] = True
        return _playlist(playlist)
    raise ValueError("unknown tool")


def _game(rom: dict, full: bool = False) -> dict:
    meta = rom.get("metadatum") or {}
    game = {
        "id": rom.get("id"),
        "name": rom.get("name") or rom.get("fs_name"),
        "system": rom.get("platform_display_name"),
        "platform_id": rom.get("platform_id"),
        "genres": meta.get("genres"),
        "first_release_date": meta.get("first_release_date"),
        "summary": _short(rom.get("summary"), 2000 if full else 300),
    }
    if full:
        keys = ("developers", "publishers", "franchises", "game_modes", "player_count", "average_rating")
        game |= {key: meta.get(key) for key in keys}
        game |= {"file": rom.get("fs_name"), "size_bytes": rom.get("fs_size_bytes"), "regions": rom.get("regions")}
    return game


def _romm(name: str, args: dict):
    if name == "list_platforms":
        keys = ("id", "name", "display_name", "slug", "rom_count")
        return [{key: item.get(key) for key in keys if key in item} for item in _dicts(_get("/api/platforms"))]
    if name == "list_collections":
        keys = ("id", "name", "description", "rom_count")
        return [{key: item.get(key) for key in keys if key in item} for item in _dicts(_get("/api/collections"))]
    if name == "get_game":
        game_id = args.get("game_id")
        if not isinstance(game_id, int) or isinstance(game_id, bool) or game_id < 1:
            raise ValueError("game_id must be a game's number")
        return _game(_get(f"/api/roms/{game_id}"), full=True)
    if name == "search_games":
        params = {
            "search_term": _text(args, "query"),
            "limit": _limit(args, 20),
            "with_char_index": "false",
            "with_filter_values": "false",
            "with_rom_id_index": "false",
        }
        platform = args.get("platform_id")
        if platform is not None:
            if not isinstance(platform, int) or isinstance(platform, bool) or platform < 1:
                raise ValueError("platform_id must be a system's number")
            params["platform_ids"] = platform
        found = _get("/api/roms", params)
        return {"total": found.get("total"), "games": [_game(rom) for rom in _dicts(found.get("items"))]}
    raise ValueError("unknown tool")


def _calendar_body(args: dict) -> bytes:
    return json.dumps(
        {
            "title": _text(args, "title"),
            "start": _text(args, "start"),
            "end": _text(args, "end"),
            "all_day": bool(args.get("all_day", False)),
            "revision": str(args.get("revision") or ""),
        }
    ).encode()


def _calendar(name: str, args: dict):
    """The caller's own calendar, through Mu3Lab's control plane; nothing else is reachable."""
    base = "/api/v1/chat-tools/calendar/events"
    if name == "list_events":
        query = {key: str(args[key]) for key in ("start", "end", "query") if args.get(key)}
        query["limit"] = str(_limit(args, 50))
        return _get(base, query)
    if name == "create_event":
        return json.loads(_request(base, "POST", _calendar_body(args))[0] or b"{}")
    event = _id(args, "event_id")
    if name == "update_event":
        return json.loads(_request(f"{base}/{event}", "PUT", _calendar_body(args))[0] or b"{}")
    if name == "delete_event":
        payload = json.dumps({"revision": str(args.get("revision") or "")}).encode()
        return json.loads(_request(f"{base}/{event}", "DELETE", payload)[0] or b"{}")
    raise ValueError("unknown tool")


def _kopia(name: str, args: dict):
    """Read-only: only the control API's two GET routes; it never starts, stops or changes a backup."""
    if name == "repository_status":
        status = _get("/api/v1/control/status")
        keys = ("connected", "description", "storage", "readonly", "encryption")
        return {key: status.get(key) for key in keys if key in status}
    if name == "backup_status":
        backups = []
        for item in _dicts(_get("/api/v1/control/sources").get("sources")):
            path = str((item.get("source") or {}).get("path") or "")
            last = item.get("lastSnapshot") if isinstance(item.get("lastSnapshot"), dict) else {}
            stats = last.get("stats") if isinstance(last.get("stats"), dict) else {}
            backups.append(
                {
                    # Mu3Lab mounts each app's data at /data/<app>.
                    "app": path.removeprefix("/data/") if path.startswith("/data/") else path,
                    "status": item.get("status"),
                    "running_now": item.get("currentTask") or None,
                    "last_started": last.get("startTime"),
                    "last_finished": last.get("endTime"),
                    "last_incomplete": last.get("incomplete") or None,
                    "last_size_bytes": stats.get("totalSize"),
                    "last_errors": stats.get("errorCount"),
                    "next_due": item.get("nextSnapshotTime"),
                }
            )
        return backups
    raise ValueError("unknown tool")


def _authentik(name: str, args: dict):
    """Read-only: the token's own Authentik role can only view people, groups and events."""
    if name == "list_people":
        found = _get("/api/v3/core/users/", {"type": "internal", "include_groups": "true", "page_size": 100})
        return [
            {
                "username": user.get("username"),
                "name": user.get("name"),
                "email": user.get("email"),
                "active": user.get("is_active"),
                "admin": user.get("is_superuser"),
                "groups": [group.get("name") for group in _dicts(user.get("groups_obj"))],
                "last_sign_in": user.get("last_login"),
            }
            for user in _dicts(found.get("results"))
        ]
    if name == "list_groups":
        found = _get("/api/v3/core/groups/", {"include_users": "false", "page_size": 100})
        return [
            {
                "name": group.get("name"),
                "admin": group.get("is_superuser"),
                "people": len(group.get("users") or []),
            }
            for group in _dicts(found.get("results"))
        ]
    if name == "recent_events":
        query = {"ordering": "-created", "page_size": _limit(args, 20)}
        action = args.get("action")
        if action is not None:
            if action not in EVENT_ACTIONS:
                raise ValueError("unknown event kind")
            query["action"] = action
        events = []
        for event in _dicts(_get("/api/v3/events/events/", query).get("results")):
            context = event.get("context") if isinstance(event.get("context"), dict) else {}
            user = event.get("user") if isinstance(event.get("user"), dict) else {}
            events.append(
                {
                    "when": event.get("created"),
                    "action": event.get("action"),
                    "username": user.get("username") or context.get("username"),
                    "from_ip": event.get("client_ip"),
                    "message": _short(context.get("message"), 200),
                }
            )
        return events
    raise ValueError("unknown tool")


HANDLERS = {
    "nextcloud": _nextcloud,
    "adventurelog": _adventurelog,
    "beaver": _beaver,
    "audiobookshelf": _audiobookshelf,
    "navidrome": _navidrome,
    "romm": _romm,
    "authentik": _authentik,
    "kopia": _kopia,
    "calendar": _calendar,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self._send(200, {"ok": bool(APP_URL)})

    def do_POST(self):
        if self.path != "/mcp":
            self.send_error(404)
            return
        authorization = self.headers.get("Authorization", "")
        if MODE == "calendar":
            # No key of its own: the caller's key goes on to Mu3Lab, which decides whose calendar it is.
            if not authorization.startswith("Bearer mu3cal_"):
                self.send_error(401)
                return
            CALLER.authorization = authorization
        else:
            token = os.environ.get("MCP_AUTH_TOKEN", "")
            if not token or not secrets.compare_digest(authorization, "Bearer " + token):
                self.send_error(401)
                return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_INPUT:
                raise ValueError("request exceeds the size limit")
            request = json.loads(self.rfile.read(size))
            method = request.get("method", "")
            request_id = request.get("id")
            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": f"mu3lab-{MODE}", "version": "1.0"},
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name", "")
                if name not in {item["name"] for item in TOOLS}:
                    raise ValueError("unknown tool")
                args = params.get("arguments") or {}
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
                output = HANDLERS[MODE](name, args)
                result = {"content": [{"type": "text", "text": json.dumps(output)}]}
            else:
                raise ValueError("unsupported MCP method")
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": locals().get("request_id"),
                    "error": {"code": -32000, "message": str(exc)[:200]},
                },
            )

    def _send(self, status: int, value: dict):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def _interface_address(name: str) -> str:
    """The IPv4 address of one network interface (SIOCGIFADDR)."""
    import fcntl
    import socket
    import struct

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        packed = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))
    return socket.inet_ntoa(packed[20:24])


def _listeners() -> list[tuple[str, int]]:
    """Where to listen: inside its own container, everywhere on 8080. On the host network (Kopia,
    which listens on loopback only), ``MCP_LISTEN`` lists ``host:port`` pairs, where ``@docker0``
    means that interface's address, the one containers reach as ``host-gateway``."""
    spec = os.environ.get("MCP_LISTEN", "")
    if not spec:
        return [("0.0.0.0", 8080)]
    found = []
    for item in spec.split(","):
        host, _, port = item.strip().rpartition(":")
        found.append((_interface_address(host[1:]) if host.startswith("@") else host, int(port)))
    return found


def _serve() -> None:
    import threading

    *others, last = [ThreadingHTTPServer(address, Handler) for address in _listeners()]
    for server in others:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    last.serve_forever()


_serve()
