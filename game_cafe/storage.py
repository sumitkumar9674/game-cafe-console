"""Small per-PC SQLite store for identity, pool state, and sync metadata."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

from .security import (create_admin_signing_record, create_node_keypair,
                       node_public_from_private, sign_node, verify_node,
                       verify_admin_proof)


PASSWORD_ITERATIONS = 600_000
HISTORY_PER_PC = 20


def default_data_path() -> Path:
    """Keep mutable state outside source and PyInstaller extraction folders."""
    override = os.environ.get("GAME_CAFE_DATA_DIR")
    base = Path(override) if override else Path(
        os.environ.get("LOCALAPPDATA", str(Path.home()))
    ) / "GameCafeConsole"
    base.mkdir(parents=True, exist_ok=True)
    return base / "console.sqlite3"


def hash_password(password: str) -> dict:
    if len(password) < 10:
        raise ValueError("Admin password must have at least 10 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                 PASSWORD_ITERATIONS)
    return {"salt": salt.hex(), "hash": digest.hex(),
            "iterations": PASSWORD_ITERATIONS}


def verify_password(password: str, record: dict) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                     int(record["iterations"]))
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False


def new_pool(cafe_name: str, pc_id: str, pc_name: str,
             admin_name: str, password: str,
             pc_public_key: str) -> tuple[dict, str]:
    """Make the first authoritative pool snapshot and random pool key."""
    for label, value in (("Cafe name", cafe_name), ("PC name", pc_name),
                         ("Admin name", admin_name)):
        if not value.strip():
            raise ValueError(f"{label} is required.")
    pool_id = uuid.uuid4().hex
    signing = create_admin_signing_record(password)
    snapshot = {
        "pool_id": pool_id, "revision": 1,
        "cafe_name": cafe_name.strip(),
        "logo": None,
        "developer_website": "",
        "developer_contact": "",
        "admin_name": admin_name.strip(),
        "admin_password": hash_password(password),
        "admin_signing": signing,
        "admin_signing_keys": [signing["public"]],
        "members": {pc_id: {"name": pc_name.strip(),
                            "public_key": pc_public_key,
                            "last_connected_at": time.time()}},
        "settings": {"grace_minutes": 10, "auto_signout_enabled": False,
                     "auto_signout_minutes": 30,
                     "computer_sort": "recent"},
        "sessions": {}, "history": {},
        "active_admin": {"pc_id": pc_id, "term": 1,
                         "expires_at": time.time() + 15, "proof": None},
        "unlock_requests": {},
    }
    return snapshot, secrets.token_hex(32)


class Store:
    """One SQLite connection protected by a lock for Tk and network threads."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_data_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=3000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS local (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pools (
                pool_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL, secret TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS peers (
                pc_id TEXT PRIMARY KEY, ip TEXT NOT NULL,
                last_seen REAL NOT NULL, ack_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS pending_joins (
                request_id TEXT PRIMARY KEY, pc_id TEXT NOT NULL,
                proposed_name TEXT NOT NULL, ip TEXT NOT NULL,
                public_key TEXT NOT NULL,
                created_at REAL NOT NULL, decision TEXT NOT NULL DEFAULT 'pending',
                sealed_welcome TEXT
            );
            CREATE TABLE IF NOT EXISTS local_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owner_records (
                pool_id TEXT NOT NULL, pc_id TEXT NOT NULL,
                version INTEGER NOT NULL, payload TEXT NOT NULL,
                signature TEXT NOT NULL,
                PRIMARY KEY (pool_id, pc_id)
            );
            CREATE TABLE IF NOT EXISTS recovery_checkpoints (
                pool_id TEXT NOT NULL, pc_id TEXT NOT NULL,
                run_id TEXT NOT NULL, session_id TEXT NOT NULL,
                activity_at REAL NOT NULL, session TEXT NOT NULL,
                PRIMARY KEY (pool_id, pc_id)
            );
        """)
        # Additive migration: keep existing identities, pools, and join history.
        join_columns = {row[1] for row in self.db.execute(
            "PRAGMA table_info(pending_joins)"
        )}
        for column, definition in (
            ("pairing_public", "TEXT"), ("pairing_admin_public", "TEXT"),
            ("pairing_secret", "TEXT"), ("expires_at", "REAL"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in join_columns:
                self.db.execute(f"ALTER TABLE pending_joins ADD COLUMN {column} {definition}")
        self.db.commit()
        if not self.local("pc_id"):
            self.set_local("pc_id", uuid.uuid4().hex)

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @property
    def pc_id(self) -> str:
        return self.local("pc_id") or ""

    def node_private_key(self) -> str:
        private = self.local("node_private_key")
        if not private:
            private, _ = create_node_keypair()
            self.set_local("node_private_key", private)
        return private

    def node_public_key(self) -> str:
        return node_public_from_private(self.node_private_key())

    @property
    def current_pool_id(self) -> str | None:
        return self.local("current_pool_id")

    def local(self, key: str) -> str | None:
        with self._lock:
            row = self.db.execute("SELECT value FROM local WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def set_local(self, key: str, value: str) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO local(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
            )

    def delete_local(self, key: str) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM local WHERE key=?", (key,))

    def snapshot(self, pool_id: str | None = None) -> dict | None:
        pool_id = pool_id or self.current_pool_id
        if not pool_id:
            return None
        with self._lock:
            row = self.db.execute(
                "SELECT snapshot FROM pools WHERE pool_id=?", (pool_id,)
            ).fetchone()
            if not row:
                return None
            result = json.loads(row[0])
            for (payload_text,) in self.db.execute(
                    "SELECT payload FROM owner_records WHERE pool_id=?", (pool_id,)):
                payload = json.loads(payload_text)
                owner = payload["pc_id"]
                result["sessions"].pop(owner, None)
                if payload["session"] is not None:
                    result["sessions"][owner] = payload["session"]
                history = {item["id"]: item for item in
                           result["history"].get(owner, [])}
                history.update({item["id"]: item for item in payload["history"]})
                result["history"][owner] = sorted(
                    history.values(), key=lambda item: item["ended_at"]
                )[-HISTORY_PER_PC:]
            return result

    def owner_records(self) -> list[dict]:
        with self._lock:
            return [{"payload": json.loads(row[0]), "signature": row[1]}
                    for row in self.db.execute(
                        "SELECT payload, signature FROM owner_records WHERE pool_id=?",
                        (self.current_pool_id,))]

    def save_owner_record(self, session: dict | None, history: list[dict],
                          grants: list[dict], run_id: str,
                          activity_at: float) -> dict:
        """Persist the owner's signed state and snapshot in one transaction."""
        with self._lock, self.db:
            snap = self.snapshot()
            owner = self.pc_id
            previous = next((item["payload"] for item in self.owner_records()
                             if item["payload"]["pc_id"] == owner), None)
            legacy = previous.get("legacy", {}) if previous else {
                item["id"]: item["paid_minutes"]
                for item in ([snap["sessions"][owner]] if owner in snap["sessions"] else [])
                + snap["history"].get(owner, [])
            }
            retained = history[-HISTORY_PER_PC:]
            retained_ids = {item["id"] for item in retained}
            if session:
                retained_ids.add(session["id"])
            grants = [item for item in grants
                      if item["command"]["session_id"] in retained_ids]
            legacy = {key: value for key, value in legacy.items()
                      if key in retained_ids}
            payload = {"pool_id": snap["pool_id"], "pc_id": owner,
                       "version": (previous["version"] + 1) if previous else 1,
                       "session": session, "history": retained,
                       "grants": grants, "legacy": legacy}
            signature = sign_node(self.node_private_key(), payload)
            self.db.execute(
                "INSERT INTO owner_records VALUES(?,?,?,?,?) ON CONFLICT(pool_id,pc_id) "
                "DO UPDATE SET version=excluded.version,payload=excluded.payload,"
                "signature=excluded.signature",
                (snap["pool_id"], owner, payload["version"],
                 json.dumps(payload, separators=(",", ":")), signature))
            if session:
                self.db.execute(
                    "INSERT INTO recovery_checkpoints VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(pool_id,pc_id) DO UPDATE SET run_id=excluded.run_id,"
                    "session_id=excluded.session_id,activity_at=excluded.activity_at,"
                    "session=excluded.session",
                    (snap["pool_id"], owner, run_id, session["id"], activity_at,
                     json.dumps(session, separators=(",", ":"))))
            else:
                self.db.execute("DELETE FROM recovery_checkpoints WHERE pool_id=? AND pc_id=?",
                                (snap["pool_id"], owner))
            return {"payload": payload, "signature": signature}

    def merge_owner_record(self, record: dict) -> bool:
        """Accept a newer registered owner's signed record, never a stale one."""
        payload = record["payload"]
        with self._lock, self.db:
            snap = self.snapshot()
            owner = payload["pc_id"]
            if payload["pool_id"] != snap["pool_id"] or owner not in snap["members"]:
                raise PermissionError("Unknown session owner.")
            verify_node(snap["members"][owner]["public_key"], payload,
                        record["signature"])
            previous = self.db.execute(
                "SELECT version,payload FROM owner_records WHERE pool_id=? AND pc_id=?",
                (snap["pool_id"], owner)).fetchone()
            if previous and payload["version"] <= previous[0]:
                return False
            if previous:
                old = json.loads(previous[1])
                old_active = old["session"]
                new_active = payload["session"]
                new_history = {item["id"]: item for item in payload["history"]}
                if old_active:
                    if new_active and new_active["id"] == old_active["id"]:
                        if new_active["paid_minutes"] < old_active["paid_minutes"]:
                            raise PermissionError("Paid allocation cannot shrink.")
                    elif old_active["id"] not in new_history:
                        raise PermissionError("A completed session needs a history record.")
                old_ids = [item["id"] for item in old["history"]]
                if any(item not in new_history for item in old_ids[-19:]):
                    raise PermissionError("Owner history cannot be erased.")
            history_ids = [item["id"] for item in payload["history"]]
            if len(history_ids) != len(set(history_ids)):
                raise PermissionError("Duplicate session history ID.")
            if payload["session"] and payload["session"]["id"] in history_ids:
                raise PermissionError("A completed session cannot reopen.")
            # Paid allotments must be backed by signed Admin commands.
            grants_by_session: dict[str, int] = dict(payload.get("legacy", {}))
            baseline = {item["id"]: item["paid_minutes"] for item in
                        snap["history"].get(owner, [])}
            existing = snap["sessions"].get(owner)
            if existing:
                baseline[existing["id"]] = existing["paid_minutes"]
            for session_id, minutes in payload.get("legacy", {}).items():
                if baseline.get(session_id, -1) < minutes:
                    raise PermissionError("Legacy session is not in the saved Admin snapshot.")
            seen_commands: set[str] = set()
            for grant in payload["grants"]:
                command = grant["command"]
                verify_node(snap["members"][command["admin_id"]]["public_key"],
                            command, grant["signature"])
                if command["target"] != owner or command["pool_id"] != snap["pool_id"]:
                    raise PermissionError("Invalid session grant target.")
                if command["id"] in seen_commands:
                    raise PermissionError("Duplicate Admin command grant.")
                seen_commands.add(command["id"])
                if not any(self._grant_proof_valid(key, command, grant)
                           for key in snap.get("admin_signing_keys",
                                               [snap["admin_signing"]["public"]])):
                    raise PermissionError("Admin grant has no valid password-backed proof.")
                if command["action"] == "start":
                    grants_by_session[command["session_id"]] = int(
                        command["details"].get("paid_minutes", 0))
                elif command["action"] == "add" and command["session_id"] in grants_by_session:
                    grants_by_session[command["session_id"]] += int(
                        command["details"]["minutes"])
            active = payload["session"]
            if active and active["paid_minutes"] > grants_by_session.get(active["id"], -1):
                raise PermissionError("Session exceeds authenticated Admin grants.")
            for item in payload["history"]:
                if (item["pc_id"] != owner or
                        item["paid_minutes"] > grants_by_session.get(item["id"], -1)):
                    raise PermissionError("History lacks authenticated Admin grants.")
            self.db.execute(
                "INSERT INTO owner_records VALUES(?,?,?,?,?) ON CONFLICT(pool_id,pc_id) "
                "DO UPDATE SET version=excluded.version,payload=excluded.payload,"
                "signature=excluded.signature",
                (snap["pool_id"], owner, payload["version"],
                 json.dumps(payload, separators=(",", ":")), record["signature"]))
            return True

    @staticmethod
    def _grant_proof_valid(public_key: str, command: dict, grant: dict) -> bool:
        try:
            verify_admin_proof(public_key, command, grant["admin_proof"])
            return True
        except Exception:
            return False

    def save_checkpoint(self, run_id: str, session: dict, activity_at: float) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO recovery_checkpoints VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(pool_id,pc_id) DO UPDATE SET run_id=excluded.run_id,"
                "session_id=excluded.session_id,activity_at=excluded.activity_at,"
                "session=excluded.session",
                (self.current_pool_id, self.pc_id, run_id, session["id"],
                 activity_at, json.dumps(session, separators=(",", ":"))))

    def checkpoint(self) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT run_id,session_id,activity_at,session FROM recovery_checkpoints "
                "WHERE pool_id=? AND pc_id=?",
                (self.current_pool_id, self.pc_id)).fetchone()
            return ({"run_id": row[0], "session_id": row[1],
                     "activity_at": row[2], "session": json.loads(row[3])}
                    if row else None)

    def clear_checkpoint(self) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM recovery_checkpoints WHERE pool_id=? AND pc_id=?",
                            (self.current_pool_id, self.pc_id))

    def secret(self, pool_id: str | None = None) -> str | None:
        pool_id = pool_id or self.current_pool_id
        if not pool_id:
            return None
        with self._lock:
            row = self.db.execute(
                "SELECT secret FROM pools WHERE pool_id=?", (pool_id,)
            ).fetchone()
            return row[0] if row else None

    def save_pool(self, snapshot: dict, secret: str, join: bool = False) -> None:
        """Save snapshot before changing membership; an old pool stays recoverable."""
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO pools(pool_id,revision,snapshot,secret) VALUES(?,?,?,?) "
                "ON CONFLICT(pool_id) DO UPDATE SET revision=excluded.revision, "
                "snapshot=excluded.snapshot, secret=excluded.secret",
                (snapshot["pool_id"], snapshot["revision"],
                 json.dumps(snapshot, separators=(",", ":")), secret),
            )
            if join:
                if self.pc_id not in snapshot["members"]:
                    raise ValueError("This PC is not registered in the destination pool.")
                self.db.execute(
                    "INSERT INTO local(key,value) VALUES('current_pool_id',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (snapshot["pool_id"],),
                )

    def update(self, change) -> dict:
        """Apply an authoritative local mutation as one durable revision."""
        with self._lock:
            snapshot = self.snapshot()
            if snapshot is None:
                raise RuntimeError("This PC has not joined a cafe.")
            result = copy.deepcopy(snapshot)
            change(result)
            result["revision"] += 1
            self.save_pool(result, self.secret() or "")
            return result

    def apply_snapshot(self, incoming: dict,
                       follow_live_admin: bool = False) -> bool:
        """Prefer higher claims; an inactive PC may follow a verified live Admin."""
        with self._lock:
            current = self.snapshot(incoming.get("pool_id"))
            if current is None or self.pc_id not in incoming.get("members", {}):
                return False
            old_admin = current["active_admin"]
            new_admin = incoming["active_admin"]
            old_rank = (int(old_admin.get("term", 0)), old_admin.get("pc_id") or "")
            new_rank = (int(new_admin.get("term", 0)), new_admin.get("pc_id") or "")
            if (new_rank < old_rank and not follow_live_admin) or \
                    (new_rank == old_rank
                     and incoming["revision"] <= current["revision"]):
                return False
            self.save_pool(incoming, self.secret(incoming["pool_id"]) or "")
            return True

    def note_peer(self, pc_id: str, ip: str, ack_revision: int | None = None) -> None:
        with self._lock, self.db:
            if ack_revision is None:
                self.db.execute(
                    "INSERT INTO peers(pc_id,ip,last_seen) VALUES(?,?,?) "
                    "ON CONFLICT(pc_id) DO UPDATE SET ip=excluded.ip, "
                    "last_seen=excluded.last_seen", (pc_id, ip, time.time())
                )
            else:
                self.db.execute(
                    "INSERT INTO peers(pc_id,ip,last_seen,ack_revision) VALUES(?,?,?,?) "
                    "ON CONFLICT(pc_id) DO UPDATE SET ip=excluded.ip, "
                    "last_seen=excluded.last_seen, "
                    "ack_revision=excluded.ack_revision",
                    (pc_id, ip, time.time(), ack_revision)
                )

    def peers(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT pc_id,ip,last_seen,ack_revision FROM peers"
            ).fetchall()
            return [dict(zip(("pc_id", "ip", "last_seen", "ack_revision"), row))
                    for row in rows]

    def remember_address(self, pc_id: str, ip: str) -> None:
        """Discovery provides an address, not authenticated ONLINE evidence."""
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO peers(pc_id,ip,last_seen) VALUES(?,?,0) "
                "ON CONFLICT(pc_id) DO UPDATE SET ip=excluded.ip", (pc_id, ip)
            )

    def record_join(self, request_id: str, pc_id: str, name: str,
                    ip: str, public_key: str, pairing_public: str) -> None:
        with self._lock, self.db:
            now = time.time()
            self.db.execute(
                "UPDATE pending_joins SET decision='expired' "
                "WHERE pc_id=? AND request_id<>? AND decision='pending'",
                (pc_id, request_id)
            )
            self.db.execute(
                "INSERT OR IGNORE INTO pending_joins"
                "(request_id,pc_id,proposed_name,ip,public_key,created_at,"
                "pairing_public,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (request_id, pc_id, name, ip, public_key, now,
                 pairing_public, now + 300)
            )

    def pending_joins(self) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT request_id,pc_id,proposed_name,ip,public_key,"
                "created_at,decision,pairing_public,pairing_admin_public,"
                "pairing_secret,expires_at,attempts FROM pending_joins "
                "WHERE decision='pending' AND expires_at>? "
                "ORDER BY created_at", (time.time(),)
            ).fetchall()
            return [dict(zip(("request_id", "pc_id", "proposed_name", "ip",
                              "public_key", "created_at", "decision",
                              "pairing_public", "pairing_admin_public",
                              "pairing_secret", "expires_at", "attempts"), row))
                    for row in rows]

    def set_join_pairing(self, request_id: str, admin_public: str,
                         shared_hex: str) -> tuple[str, str]:
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT decision,expires_at,pairing_admin_public,pairing_secret "
                "FROM pending_joins WHERE request_id=?", (request_id,)
            ).fetchone()
            if not row or row[0] != "pending" or row[1] <= time.time():
                raise RuntimeError("Join request expired; generate a new code.")
            if row[2] and row[3]:
                return row[2], row[3]
            self.db.execute(
                "UPDATE pending_joins SET pairing_admin_public=?,pairing_secret=? "
                "WHERE request_id=?", (admin_public, shared_hex, request_id)
            )
            return admin_public, shared_hex

    def verify_join_code(self, request_id: str, pc_id: str,
                         entered: str, expected: str) -> str:
        error = None
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT decision,expires_at,attempts,pairing_secret "
                "FROM pending_joins WHERE request_id=? AND pc_id=?",
                (request_id, pc_id)
            ).fetchone()
            if not row or row[0] != "pending" or row[1] <= time.time():
                raise RuntimeError("Join request expired; generate a new code.")
            if not row[3]:
                raise RuntimeError("Pairing has not started.")
            if row[2] >= 5:
                raise PermissionError("Too many incorrect codes; request a new code.")
            if not hmac.compare_digest(entered, expected):
                attempts = row[2] + 1
                self.db.execute(
                    "UPDATE pending_joins SET attempts=?,decision=? WHERE request_id=?",
                    (attempts, "rejected" if attempts >= 5 else "pending",
                     request_id)
                )
                error = (
                    "Incorrect verification code. "
                    + ("Request a new code." if attempts >= 5 else
                       f"{5 - attempts} attempts remain.")
                )
        if error:
            raise PermissionError(error)
        return row[3]

    def decide_join(self, request_id: str, decision: str,
                    sealed_welcome: str | None = None) -> None:
        with self._lock, self.db:
            self.db.execute(
                "UPDATE pending_joins SET decision=?,sealed_welcome=? "
                "WHERE request_id=?", (decision, sealed_welcome, request_id)
            )

    def join_decision(self, request_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT decision,sealed_welcome,pairing_admin_public,expires_at "
                "FROM pending_joins "
                "WHERE request_id=?", (request_id,)
            ).fetchone()
            if not row:
                return None
            state = "expired" if row[0] == "pending" and row[3] <= time.time() else row[0]
            return {"state": state, "sealed_welcome": row[1],
                    "pairing_admin_public": row[2]}

    def prune_history(self, snapshot: dict) -> None:
        for pc_id, records in snapshot["history"].items():
            snapshot["history"][pc_id] = records[-HISTORY_PER_PC:]

    def post_local_command(self, action: str, payload: dict | None = None) -> None:
        """CafeConsole child posts an action to its Default-side controller."""
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO local_commands(action,payload) VALUES(?,?)",
                (action, json.dumps(payload or {}))
            )

    def take_local_commands(self) -> list[tuple[str, dict]]:
        with self._lock, self.db:
            rows = self.db.execute(
                "SELECT id,action,payload FROM local_commands ORDER BY id"
            ).fetchall()
            if rows:
                self.db.execute("DELETE FROM local_commands WHERE id<=?", (rows[-1][0],))
            return [(row[1], json.loads(row[2])) for row in rows]

    def leave_old_pool(self, old_pool_id: str) -> None:
        """Remove this PC's old local pool copy only after a new one is saved."""
        if old_pool_id == self.current_pool_id:
            raise ValueError("Cannot remove current pool data.")
        with self._lock, self.db:
            self.db.execute("DELETE FROM pools WHERE pool_id=?", (old_pool_id,))
            self.db.execute("DELETE FROM peers")
            self.db.execute("DELETE FROM pending_joins")
