"""Consistent copies of each app's databases, so a backup never stops an app."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ctl import database_dumps
from ctl.registry import load
from ctl.runtime import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


def run_script(script: str, *args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    """One helper script, run here instead of in its container."""
    return subprocess.run([sys.executable, "-c", script, *args], input=stdin, capture_output=True, timeout=60)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Nothing installed, so each app's own Compose files decide.
        self.paths = RuntimePaths(Path(temporary.name))
        self.registry = load()

    def test_every_catalog_database_is_found_with_a_folder_of_its_own(self):
        found = {service.id: database_dumps.databases(service, ROOT, self.paths) for service in self.registry.services}
        self.assertEqual(
            [(item.service, item.engine, item.path) for item in found["immich"]],
            [("database", "postgres", "immich/postgres")],
        )
        self.assertEqual([item.engine for item in found["romm"]], ["mariadb"])
        self.assertEqual([item.path for item in found["paperless-ngx"]], ["paperless/postgres"])  # Postgres 18's layout
        for app, items in found.items():
            for item in items:
                # The dump sits beside the live database folder, never inside the folder Kopia leaves out.
                self.assertIn("/", item.path, app)

    def test_caches_and_live_databases_are_left_out_but_files_people_made_are_not(self):
        # SurfSense keeps only SQLite, which is copied online and saved, never left out.
        self.assertEqual(database_dumps.ignored(self.registry.get("surfsense"), ROOT, self.paths), [])
        self.assertEqual(database_dumps.ignored(self.registry.get("romm"), ROOT, self.paths), ["romm/db", "romm/redis"])
        self.assertEqual(
            database_dumps.kopia_rules(self.registry.get("immich"), ROOT, self.paths), ["/immich/postgres/"]
        )
        self.assertFalse(self.registry.get("speaches").manifest.backup.include)  # its models download again

    def test_each_manifest_ignore_names_a_folder_of_its_own_app(self):
        for service in self.registry.services:
            folders = set(database_dumps._folders(service, ROOT, self.paths))
            for path in service.manifest.backup.ignore:
                with self.subTest(app=service.id, path=path):
                    self.assertIn(path.split("/", 1)[0], folders)


class ScriptTests(unittest.TestCase):
    """The helper scripts themselves, on real files and a real SQLite."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_a_dump_is_kept_only_once_it_is_complete(self):
        target = self.folder / ".mu3lab-backup" / "db.sql"
        done = run_script(
            database_dumps._WRITE, str(target), "dump complete", stdin=b"CREATE TABLE t;\n-- dump complete\n"
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        cut = run_script(database_dumps._WRITE, str(target), "dump complete", stdin=b"CREATE TABLE u;\n")
        self.assertNotEqual(cut.returncode, 0)
        # The last complete dump stays, and nothing half-written is left beside it.
        self.assertIn(b"TABLE t", target.read_bytes())
        self.assertEqual(sorted(path.name for path in target.parent.iterdir()), ["db.sql"])

    def test_a_postgres_dump_keeps_its_search_path_when_loaded(self):
        dump = self.folder / "db.sql"
        dump.write_bytes(b"SELECT pg_catalog.set_config('search_path', '', false);\nSELECT 1;\n")
        loaded = run_script(database_dumps._READ, str(dump), "postgres").stdout
        self.assertIn(b"'public, pg_catalog', true", loaded)
        self.assertEqual(run_script(database_dumps._READ, str(dump), "mariadb").stdout, dump.read_bytes())

    def test_sqlite_databases_are_copied_while_open_and_left_out_folders_are_not_searched(self):
        (self.folder / "app").mkdir()
        (self.folder / "cache").mkdir()
        live = sqlite3.connect(self.folder / "app" / "app.db")
        live.execute("PRAGMA journal_mode=wal")
        live.execute("CREATE TABLE notes (text)")
        live.execute("INSERT INTO notes VALUES ('kept')")
        live.commit()  # still open, its newest rows only in the write-ahead log
        sqlite3.connect(self.folder / "cache" / "cache.db").execute("CREATE TABLE c (x)")
        (self.folder / "app" / "photo.jpg").write_bytes(os.urandom(4096))
        result = self.sqlite(["cache", ".mu3lab-backup"], [])
        self.assertEqual(json.loads(result.stdout), {"copied": ["app/app.db"], "failed": []}, result.stderr)
        live.close()
        copy = sqlite3.connect(self.folder / ".mu3lab-backup" / "sqlite" / "app" / "app.db")
        self.assertEqual(copy.execute("SELECT text FROM notes").fetchall(), [("kept",)])
        self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone(), ("ok",))

    def sqlite(self, skip: list[str], record: list[dict]) -> subprocess.CompletedProcess:
        return run_script(
            database_dumps._SQLITE,
            str(self.folder),
            json.dumps(skip),
            json.dumps(record),
            json.dumps(database_dumps.PLAIN_SUFFIXES),
        )

    def test_photos_films_and_documents_are_never_opened_to_look_for_databases(self):
        library = self.folder / "library" / "2026"
        library.mkdir(parents=True)
        # Even one that is a database inside: a library's files are never read for the search.
        sqlite3.connect(library / "IMG_0001.JPG").execute("CREATE TABLE t (x)").connection.commit()
        sqlite3.connect(library / "notes.sqlite3").execute("CREATE TABLE t (x)").connection.commit()
        result = self.sqlite([".mu3lab-backup"], [])
        self.assertEqual(json.loads(result.stdout)["copied"], ["library/2026/notes.sqlite3"], result.stderr)

    def test_each_folder_records_its_dumps_and_drops_dumps_of_databases_it_no_longer_has(self):
        saved = self.folder / ".mu3lab-backup"
        saved.mkdir()
        (saved / "database.sql").write_text("-- current\n")
        (saved / "old-db.sql").write_text("-- a release ago\n")
        record = [{"service": "database", "engine": "postgres", "inside": "postgres"}]
        self.assertEqual(self.sqlite([".mu3lab-backup"], record).returncode, 0)
        self.assertEqual(json.loads((saved / "databases.json").read_text()), {"databases": record})
        self.assertEqual(sorted(path.name for path in saved.glob("*.sql")), ["database.sql"])
        self.assertEqual((saved / "databases.json").stat().st_mode & 0o777, 0o600)
        # A release without databases here leaves no record and no dump to load.
        self.assertEqual(self.sqlite([".mu3lab-backup"], []).returncode, 0)
        self.assertEqual(list(saved.iterdir()), [])

    def test_a_restore_puts_copies_in_place_and_empties_each_dumped_database(self):
        saved = self.folder / ".mu3lab-backup"
        (saved / "sqlite").mkdir(parents=True)
        (saved / "database.sql").write_text("-- dump\n")
        (self.folder / "postgres").mkdir()
        (self.folder / "postgres" / "PG_VERSION").write_text("14")  # a live copy an older policy saved
        (self.folder / "app.db").write_bytes(b"torn")
        (self.folder / "app.db-wal").write_bytes(b"stale")
        (saved / "sqlite" / "app.db").write_bytes(b"consistent")
        # A backup from before the record: today's release says where each database lives.
        result = run_script(
            database_dumps._PREPARE, str(self.folder), json.dumps({"database": "postgres", "other": "x"})
        )
        self.assertEqual(json.loads(result.stdout), {"load": ["database"], "placed": ["app.db"]}, result.stderr)
        self.assertEqual(list((self.folder / "postgres").iterdir()), [])
        self.assertEqual((self.folder / "app.db").read_bytes(), b"consistent")
        self.assertFalse((self.folder / "app.db-wal").exists())

    def test_a_restore_follows_the_backups_own_record_over_todays_release(self):
        saved = self.folder / ".mu3lab-backup"
        saved.mkdir()
        (saved / "db.sql").write_text("-- dump\n")
        (saved / "databases.json").write_text(
            json.dumps({"databases": [{"service": "db", "engine": "postgres", "inside": "pgdata"}]})
        )
        (self.folder / "pgdata").mkdir()
        (self.folder / "pgdata" / "PG_VERSION").write_text("16")
        # Today's release renamed the service and moved its folder; the backup's names decide.
        result = run_script(database_dumps._PREPARE, str(self.folder), json.dumps({"database": "postgres"}))
        self.assertEqual(json.loads(result.stdout)["load"], ["db"], result.stderr)
        self.assertEqual(list((self.folder / "pgdata").iterdir()), [])
        # A record that points outside the folder is refused before anything is emptied.
        (saved / "databases.json").write_text(
            json.dumps({"databases": [{"service": "db", "engine": "postgres", "inside": "../elsewhere"}]})
        )
        refused = run_script(database_dumps._PREPARE, str(self.folder), "{}")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(b"cannot place", refused.stderr)


class OrchestrationTests(unittest.TestCase):
    """Which containers start, what runs where, and what counts as a failure (Docker itself is not run)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name))
        (self.paths.data / "immich").mkdir(parents=True)
        self.immich = load().get("immich")
        self.commands: list[list[str]] = []
        self.pipes: list[tuple[list[str], list[str]]] = []
        self.pipe_result = (0, "")
        self.running = ""
        self.ps_result = 0
        self.sqlite_result = (0, json.dumps({"copied": ["library/x.db"], "failed": []}))
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(database_dumps.app_control, "project", return_value=self.paths.projects))
        stack.enter_context(patch.object(database_dumps.compose_settings, "settings", return_value=({}, [])))
        stack.enter_context(patch.object(database_dumps.actions, "docker_cmd", side_effect=self.docker))
        self.up = stack.enter_context(patch.object(database_dumps.actions, "compose_up", return_value=(0, "")))
        stack.enter_context(patch.object(database_dumps.actions, "docker_pipe", side_effect=self.pipe))

    def docker(self, argv, _log, **_kwargs):
        self.commands.append(argv)
        if "ps" in argv:
            return (0, self.running) if self.ps_result == 0 else (self.ps_result, "context deadline exceeded")
        if database_dumps._SQLITE in argv:
            return self.sqlite_result
        return 0, ""

    def pipe(self, producer, consumer, _log, **_kwargs):
        self.pipes.append((producer, consumer))
        return self.pipe_result

    def test_a_running_database_is_dumped_where_it_runs_and_left_running(self):
        self.running = "database\nimmich-server\n"
        detail = database_dumps.write(self.immich, ROOT, lambda _l: None, self.paths)
        self.assertEqual(detail, "Copied Immich's databases (1 database dump, 1 SQLite copy).")
        producer, consumer = self.pipes[0]
        self.assertIn("database", producer)
        self.assertIn("pg_dumpall", producer[-1])
        self.assertIn("/folder/.mu3lab-backup/database.sql", consumer)
        self.up.assert_not_called()
        self.assertFalse([argv for argv in self.commands if "stop" in argv])

    def test_a_stopped_apps_database_starts_alone_for_its_dump_and_stops_again(self):
        database_dumps.write(self.immich, ROOT, lambda _l: None, self.paths)
        self.assertEqual(self.up.call_args.kwargs["services"], ["database"])
        self.assertEqual([argv[-2:] for argv in self.commands if "stop" in argv], [["stop", "database"]])

    def test_not_knowing_what_runs_fails_the_backup_rather_than_stop_a_database_in_use(self):
        self.ps_result = 1
        with self.assertRaisesRegex(database_dumps.DumpFailed, "which of Immich's services run"):
            database_dumps.write(self.immich, ROOT, lambda _l: None, self.paths)
        self.up.assert_not_called()
        self.assertFalse([argv for argv in self.commands if "stop" in argv])
        self.assertFalse(self.pipes)

    def test_each_folder_with_dumps_records_them_and_a_failed_record_fails_the_backup(self):
        self.running = "database\n"
        database_dumps.write(self.immich, ROOT, lambda _l: None, self.paths)
        sqlite = next(argv for argv in self.commands if database_dumps._SQLITE in argv)
        record = json.loads(sqlite[sqlite.index(database_dumps._SQLITE) + 3])
        self.assertEqual(record, [{"service": "database", "engine": "postgres", "inside": "postgres"}])
        self.sqlite_result = (1, "docker: no space left on device")
        with self.assertRaisesRegex(database_dumps.DumpFailed, "could not be recorded"):
            database_dumps.write(self.immich, ROOT, lambda _l: None, self.paths)

    def test_a_dump_the_release_in_place_has_no_database_for_fails_the_restore(self):
        with self.assertRaisesRegex(database_dumps.DumpFailed, r"\(db\) that the release in place does not have"):
            database_dumps.load(self.immich, ROOT, ["database", "db"], lambda _l: None, self.paths)
        self.assertFalse(self.pipes)

    def test_a_dump_that_fails_fails_the_backup(self):
        self.pipe_result = (1, "pg_dumpall: error: connection failed")
        with self.assertRaisesRegex(database_dumps.DumpFailed, "could not be copied"):
            database_dumps.write(self.immich, ROOT, lambda _l: None, self.paths)

    def test_loading_tolerates_only_what_a_fresh_database_already_has(self):
        self.pipe_result = (0, 'psql:<stdin>:5: ERROR:  role "postgres" already exists\n')
        database_dumps.load(self.immich, ROOT, ["database"], lambda _l: None, self.paths)
        self.assertIn("psql", self.pipes[0][1][-1])
        self.pipe_result = (0, 'psql:<stdin>:9: ERROR:  type "vector" does not exist\n')
        with self.assertRaisesRegex(database_dumps.DumpFailed, "could not be loaded"):
            database_dumps.load(self.immich, ROOT, ["database"], lambda _l: None, self.paths)


if __name__ == "__main__":
    unittest.main()
