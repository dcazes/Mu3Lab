"""Shared, isolated gateway authority; no control-plane secrets or host execution.

Both the gateway and authenticated dashboard use this store. Tool arguments
and provisioned credentials are encrypted; history contains only identities,
bindings, decisions and outcomes. There is no automatic write redispatch.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from jsonschema import Draft202012Validator

SUBJECT_TTL = 600
APPROVAL_TTL = 300
TERMINAL = {"succeeded", "failed", "outcome_unknown", "rejected", "revoked", "expired"}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def schema_digest(schema: object) -> str:
    """Pin for a tool's verified input schema; an absent schema pins the empty object schema."""
    return hashlib.sha256(canonical(schema or {"type": "object", "properties": {}}).encode()).hexdigest()


def validate_arguments(schema: dict, arguments: dict) -> None:
    if not isinstance(arguments, dict) or len(canonical(arguments).encode()) > 64_000:
        raise ValueError("Tool arguments must be an object within the 64 KB limit.")
    Draft202012Validator.check_schema(schema)
    if next(Draft202012Validator(schema).iter_errors(arguments), None):
        # Validator messages may contain private values; do not copy them into diagnostics.
        raise ValueError("Tool arguments do not match the connector's verified input schema.")


def validate_policy(policy: object) -> None:
    """Validate the authorization fields before any app entry can be used."""
    if not isinstance(policy, dict) or policy.get("version") != 2 or not isinstance(policy.get("apps"), dict):
        raise ValueError("gateway policy is malformed")
    if type(policy.get("revision")) is not int or policy["revision"] < 1:
        raise ValueError("gateway policy revision is malformed")
    for app in policy["apps"].values():
        if not isinstance(app, dict):
            raise ValueError("gateway app is malformed")
        if (
            app.get("data_scope") != "operator_only"
            or app.get("audience") != "operators"
            or app.get("delegation") != "none"
        ):
            raise ValueError("gateway data scope is unsupported")
        if not isinstance(app.get("connector_revision"), str) or type(app.get("approval_required")) is not bool:
            raise ValueError("gateway approval contract is malformed")
        if not isinstance(app.get("upstream"), dict) or not isinstance(app["upstream"].get("url"), str):
            raise ValueError("gateway upstream is malformed")
        if not isinstance(app.get("tools"), dict) or not isinstance(app.get("categories"), list):
            raise ValueError("gateway tools are malformed")
        categories = {}
        for category in app["categories"]:
            if not isinstance(category, dict) or not isinstance(category.get("id"), str):
                raise ValueError("gateway category is malformed")
            if type(category.get("enabled")) is not bool or category["id"] in categories:
                raise ValueError("gateway category switch is malformed")
            categories[category["id"]] = category
        for name, tool in app["tools"].items():
            if not isinstance(tool, dict) or tool.get("name") != name or tool.get("category") not in categories:
                raise ValueError("gateway tool is malformed")
            if tool.get("access") not in ("read", "write") or type(tool.get("enabled")) is not bool:
                raise ValueError("gateway tool permission is malformed")


class Authority:
    def __init__(self, directory: Path | str, *, initialize: bool = True):
        self.directory = Path(directory).resolve()
        self.database = self.directory / "authority.db"
        self.key = self.directory / "arguments.key"
        if initialize:
            self._initialize()

    def _initialize(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        with (self.directory / "initialize.lock").open("a+b") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not self.key.exists():
                if self.database.exists():
                    raise ValueError("Gateway authority key is missing; restore the matching checkpoint.")
                fd, temporary = tempfile.mkstemp(dir=self.directory)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.key)
            Fernet(self.key.read_bytes())
            fd = os.open(self.database, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.database, 0o600)
            os.chmod(self.key, 0o600)
            with closing(sqlite3.connect(self.database)) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1}:
                    raise ValueError("Gateway authority belongs to a newer release.")
                if version == 1:
                    return
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS policy (
                        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                        digest TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0);
                    INSERT OR IGNORE INTO policy VALUES (1,0,'',0);
                    CREATE TABLE IF NOT EXISTS subjects (
                        subject TEXT PRIMARY KEY, enabled INTEGER NOT NULL, valid_until REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS credentials (
                        subject TEXT NOT NULL, provider TEXT NOT NULL, app TEXT NOT NULL,
                        version INTEGER NOT NULL, token_hash TEXT NOT NULL, ciphertext BLOB NOT NULL,
                        enabled INTEGER NOT NULL, PRIMARY KEY(subject,provider,app));
                    CREATE TABLE IF NOT EXISTS operations (
                        id TEXT PRIMARY KEY, subject TEXT NOT NULL, provider TEXT NOT NULL,
                        credential_version INTEGER NOT NULL, app TEXT NOT NULL, server TEXT NOT NULL,
                        connector_revision TEXT NOT NULL, tool TEXT NOT NULL, input_hash TEXT NOT NULL,
                        schema_hash TEXT NOT NULL, arguments BLOB, policy_revision INTEGER NOT NULL,
                        state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                        dispatch_at REAL NOT NULL DEFAULT 0, completed_at REAL NOT NULL DEFAULT 0);
                    CREATE INDEX IF NOT EXISTS operations_owner ON operations(subject,created_at);
                    CREATE TABLE IF NOT EXISTS decisions (
                        operation TEXT NOT NULL, subject TEXT NOT NULL, decision TEXT NOT NULL, at REAL NOT NULL);
                    PRAGMA user_version=1;
                    COMMIT;
                """)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    @contextmanager
    def _db(self, *, write: bool = False):
        if not self.database.is_file() or not self.key.is_file():
            raise ValueError("Gateway authority is unavailable.")
        try:
            connection = sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True, timeout=10)
        except sqlite3.Error:
            raise ValueError("Gateway authority is unavailable.") from None
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        except sqlite3.Error:
            raise ValueError("Gateway authority is unavailable.") from None
        finally:
            connection.close()

    def _cipher(self) -> Fernet:
        return Fernet(self.key.read_bytes())

    def invalidate(self) -> int:
        with self._db(write=True) as db:
            db.execute("UPDATE policy SET revision=revision+1,digest='',acknowledged=0 WHERE id=1")
            db.execute("UPDATE operations SET state='revoked',arguments=NULL WHERE state IN ('pending','approved')")
            return int(db.execute("SELECT revision FROM policy").fetchone()[0])

    def publish(self, revision: int, digest: str) -> None:
        with self._db(write=True) as db:
            changed = db.execute("UPDATE policy SET digest=? WHERE revision=? AND digest=''", (digest, revision))
            if changed.rowcount != 1:
                raise ValueError("Gateway policy publication was superseded.")

    def policy_state(self) -> dict:
        with self._db() as db:
            return dict(db.execute("SELECT * FROM policy WHERE id=1").fetchone())

    def check_policy(self, revision: int, digest: str, *, acknowledge: bool = False) -> None:
        with self._db(write=acknowledge) as db:
            row = db.execute("SELECT * FROM policy WHERE id=1").fetchone()
            if not row["digest"] or row["revision"] != revision or not hmac.compare_digest(row["digest"], digest):
                raise ValueError("Gateway policy is missing, stale or not committed.")
            if acknowledge:
                db.execute("UPDATE policy SET acknowledged=? WHERE id=1", (revision,))

    def grant_subject(self, subject: str, *, operator: bool) -> None:
        if not subject:
            raise ValueError("A verified subject is required.")
        if not operator:
            self.revoke_subject(subject)
            return
        with self._db(write=True) as db:
            db.execute(
                "INSERT INTO subjects VALUES (?,1,?) ON CONFLICT(subject) DO UPDATE SET enabled=1,valid_until=excluded.valid_until",
                (subject, time.time() + SUBJECT_TTL),
            )

    def revoke_subject(self, subject: str) -> None:
        with self._db(write=True) as db:
            db.execute("UPDATE subjects SET enabled=0,valid_until=0 WHERE subject=?", (subject,))
            db.execute(
                "UPDATE credentials SET enabled=0,token_hash='',ciphertext=X'',version=version+1 WHERE subject=?",
                (subject,),
            )
            db.execute(
                "UPDATE operations SET state='revoked',arguments=NULL WHERE subject=? AND state IN ('pending','approved')",
                (subject,),
            )

    @staticmethod
    def _subject(db, subject: str) -> None:
        row = db.execute("SELECT * FROM subjects WHERE subject=?", (subject,)).fetchone()
        if not row or not row["enabled"] or row["valid_until"] <= time.time():
            raise ValueError("Operator access is missing, revoked or due for verification.")

    def credential(self, subject: str, app: str, *, provider: str = "lobehub", rotate: bool = False) -> str:
        with self._db(write=True) as db:
            self._subject(db, subject)
            row = db.execute(
                "SELECT * FROM credentials WHERE subject=? AND provider=? AND app=?", (subject, provider, app)
            ).fetchone()
            if row and row["enabled"] and not rotate:
                return self._cipher().decrypt(row["ciphertext"]).decode()
            token = secrets.token_urlsafe(32)
            version = int(row["version"]) + 1 if row else 1
            db.execute(
                "INSERT OR REPLACE INTO credentials VALUES (?,?,?,?,?,?,1)",
                (
                    subject,
                    provider,
                    app,
                    version,
                    hashlib.sha256(token.encode()).hexdigest(),
                    self._cipher().encrypt(token.encode()),
                ),
            )
            db.execute(
                "UPDATE operations SET state='revoked',arguments=NULL WHERE subject=? AND app=? AND provider=? AND state IN ('pending','approved')",
                (subject, app, provider),
            )
            return token

    def authenticate(self, token: str, app: str) -> dict:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            row = db.execute(
                "SELECT subject,provider,app,version FROM credentials WHERE token_hash=? AND app=? AND enabled=1",
                (digest, app),
            ).fetchone()
            if not row:
                raise ValueError("A current person-bound operator credential is required.")
            self._subject(db, row["subject"])
            return dict(row)

    @staticmethod
    def _permission(app: dict, tool: str) -> None:
        reviewed = app.get("tools", {}).get(tool)
        categories = {item["id"]: item for item in app.get("categories", [])}
        if (
            app.get("data_scope") != "operator_only"
            or not reviewed
            or reviewed.get("access") != "write"
            or not reviewed.get("enabled")
            or not categories.get(reviewed.get("category"), {}).get("enabled")
            or not app.get("approval_required")
        ):
            raise ValueError("This write is not enabled for operator approval.")

    def prepare(
        self, principal: dict, app_id: str, app: dict, tool: str, arguments: dict, schema: dict, revision: int
    ) -> dict:
        if principal.get("app") != app_id or principal.get("provider") == "control-discovery":
            raise ValueError("This credential cannot request approval for this app.")
        self._permission(app, tool)
        validate_arguments(schema, arguments)
        cipher = self._cipher()
        input_hash = hmac.new(self.key.read_bytes(), canonical(arguments).encode(), hashlib.sha256).hexdigest()
        schema_hash = hashlib.sha256(canonical(schema).encode()).hexdigest()
        now = time.time()
        with self._db(write=True) as db:
            self._current(db, principal, revision)
            row = db.execute(
                "SELECT * FROM operations WHERE subject=? AND provider=? AND credential_version=? AND app=? AND tool=? AND input_hash=? AND policy_revision=? AND schema_hash=? AND server=? AND connector_revision=? AND state IN ('pending','approved') AND expires_at>?",
                (
                    principal["subject"],
                    principal["provider"],
                    principal["version"],
                    app_id,
                    tool,
                    input_hash,
                    revision,
                    schema_hash,
                    app["server_id"],
                    app["connector_revision"],
                    now,
                ),
            ).fetchone()
            if row:
                return self._public(row)
            count = db.execute(
                "SELECT COUNT(*) FROM operations WHERE subject=? AND state IN ('pending','approved') AND expires_at>?",
                (principal["subject"], now),
            ).fetchone()[0]
            if count >= 20:
                raise ValueError("Too many changes await approval. Review or decline existing requests first.")
            operation = uuid4().hex
            db.execute(
                "INSERT INTO operations (id,subject,provider,credential_version,app,server,connector_revision,tool,input_hash,schema_hash,arguments,policy_revision,state,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    operation,
                    principal["subject"],
                    principal["provider"],
                    principal["version"],
                    app_id,
                    app["server_id"],
                    app["connector_revision"],
                    tool,
                    input_hash,
                    schema_hash,
                    cipher.encrypt(canonical(arguments).encode()),
                    revision,
                    "pending",
                    now,
                    now + APPROVAL_TTL,
                ),
            )
            return self._public(db.execute("SELECT * FROM operations WHERE id=?", (operation,)).fetchone())

    def _current(self, db, principal: dict, revision: int) -> None:
        self._subject(db, principal["subject"])
        row = db.execute(
            "SELECT * FROM credentials WHERE subject=? AND provider=? AND app=? AND enabled=1",
            (principal["subject"], principal["provider"], principal["app"]),
        ).fetchone()
        policy = db.execute("SELECT * FROM policy WHERE id=1").fetchone()
        if not row or row["version"] != principal["version"] or policy["revision"] != revision or not policy["digest"]:
            raise ValueError("The credential or policy changed; request fresh approval.")

    @staticmethod
    def _public(row) -> dict:
        return {
            name: row[name]
            for name in (
                "id",
                "subject",
                "provider",
                "credential_version",
                "app",
                "server",
                "tool",
                "connector_revision",
                "policy_revision",
                "state",
                "created_at",
                "expires_at",
                "dispatch_at",
                "completed_at",
            )
        }

    def view(self, operation: str, subject: str, *, arguments: bool = False) -> dict:
        with self._db(write=True) as db:
            db.execute(
                "UPDATE operations SET state='expired',arguments=NULL WHERE id=? AND subject=? AND state IN ('pending','approved') AND expires_at<=?",
                (operation, subject, time.time()),
            )
            db.execute(
                "UPDATE operations SET state='outcome_unknown',arguments=NULL,completed_at=? WHERE id=? AND subject=? AND state='dispatching' AND dispatch_at<?",
                (time.time(), operation, subject, time.time() - 180),
            )
            row = db.execute("SELECT * FROM operations WHERE id=? AND subject=?", (operation, subject)).fetchone()
            if not row:
                raise ValueError("This approval request is unavailable.")
            result = self._public(row)
            if row["state"] in {"pending", "approved"} and row["expires_at"] <= time.time():
                result["state"] = "expired"
            if arguments and row["arguments"]:
                result["arguments"] = json.loads(self._cipher().decrypt(row["arguments"]))
            return result

    def list_operations(self, subject: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM operations WHERE subject=? ORDER BY created_at DESC LIMIT 50", (subject,)
            ).fetchall()
        return [self.view(row["id"], subject) for row in rows]

    def matches_arguments(self, operation: str, subject: str, arguments: dict) -> bool:
        fingerprint = hmac.new(self.key.read_bytes(), canonical(arguments).encode(), hashlib.sha256).hexdigest()
        with self._db() as db:
            row = db.execute(
                "SELECT input_hash FROM operations WHERE id=? AND subject=?", (operation, subject)
            ).fetchone()
            return bool(row and hmac.compare_digest(row["input_hash"], fingerprint))

    def decide(self, operation: str, subject: str, *, approve: bool) -> dict:
        with self._db(write=True) as db:
            row = db.execute("SELECT * FROM operations WHERE id=? AND subject=?", (operation, subject)).fetchone()
            self._subject(db, subject)
            policy = db.execute("SELECT * FROM policy WHERE id=1").fetchone()
            if (
                not row
                or row["state"] != "pending"
                or row["expires_at"] <= time.time()
                or row["policy_revision"] != policy["revision"]
                or not policy["digest"]
            ):
                raise ValueError("Approval expired, changed or has already been decided.")
            state = "approved" if approve else "rejected"
            db.execute(
                "UPDATE operations SET state=?,arguments=CASE WHEN ? THEN arguments ELSE NULL END WHERE id=?",
                (state, int(approve), operation),
            )
            db.execute("INSERT INTO decisions VALUES (?,?,?,?)", (operation, subject, state, time.time()))
        return self.view(operation, subject)

    def claim(self, operation: str, principal: dict, app: dict, schema: dict, revision: int) -> dict | None:
        with self._db(write=True) as db:
            self._current(db, principal, revision)
            row = db.execute(
                "SELECT * FROM operations WHERE id=? AND subject=?", (operation, principal["subject"])
            ).fetchone()
            if not row:
                raise ValueError("This approval request is unavailable.")
            if row["state"] in TERMINAL or row["state"] == "dispatching":
                return None
            if row["state"] != "approved" or row["expires_at"] <= time.time():
                raise ValueError("This request has no current human approval.")
            self._permission(app, row["tool"])
            if (
                row["app"],
                row["provider"],
                row["credential_version"],
                row["policy_revision"],
                row["server"],
                row["connector_revision"],
                row["schema_hash"],
            ) != (
                principal["app"],
                principal["provider"],
                principal["version"],
                revision,
                app["server_id"],
                app["connector_revision"],
                hashlib.sha256(canonical(schema).encode()).hexdigest(),
            ):
                raise ValueError("Approval no longer matches this connector, schema or credential.")
            payload = json.loads(self._cipher().decrypt(row["arguments"]))
            validate_arguments(schema, payload)
            db.execute(
                "UPDATE operations SET state='dispatching',dispatch_at=? WHERE id=? AND state='approved'",
                (time.time(), operation),
            )
            return {"tool": row["tool"], "arguments": payload}

    def finish(self, operation: str, outcome: str) -> None:
        if outcome not in {"succeeded", "failed", "outcome_unknown"}:
            raise ValueError("Invalid operation outcome.")
        with self._db(write=True) as db:
            db.execute(
                "UPDATE operations SET state=?,arguments=NULL,completed_at=? WHERE id=? AND state='dispatching'",
                (outcome, time.time(), operation),
            )

    def recover_dispatches(self) -> None:
        with self._db(write=True) as db:
            db.execute(
                "UPDATE operations SET state='outcome_unknown',arguments=NULL,completed_at=? WHERE state='dispatching'",
                (time.time(),),
            )
            db.execute(
                "UPDATE operations SET state='expired',arguments=NULL WHERE state IN ('pending','approved') AND expires_at<=?",
                (time.time(),),
            )
