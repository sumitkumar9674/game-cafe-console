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

from .customers import (DEFAULT_CUSTOMER_PASSWORD, MAX_RETAINER_SECONDS,
                        normalize_customer_name, validate_cooldown_minutes,
                        validate_customer_password)
from .security import (create_admin_signing_record, create_node_keypair,
                       node_public_from_private, sign_node, verify_node,
                       verify_admin_proof)


PASSWORD_ITERATIONS = 600_000
HISTORY_PER_PC = 100
NOTIFICATION_LIMIT = 100
MAX_WALLPAPER_BYTES = 500_000


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


def hash_customer_password(password: str = DEFAULT_CUSTOMER_PASSWORD) -> dict:
    """Use the project PBKDF2 verifier without the Admin password length rule."""
    if not isinstance(password, str) or not password:
        raise ValueError("Customer password is required.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                 PASSWORD_ITERATIONS)
    return {"salt": salt.hex(), "hash": digest.hex(),
            "iterations": PASSWORD_ITERATIONS}


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
                     "computer_sort": "recent",
                     "retainer_restart_cooldown_minutes": 0,
                     "customer_login_cooldown_minutes": 0},
        "wallpaper": None,
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
            CREATE TABLE IF NOT EXISTS customer_accounts (
                pool_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                password_verifier TEXT,
                balance_seconds INTEGER NOT NULL,
                created_at REAL NOT NULL,
                modified_at REAL NOT NULL,
                modified_by TEXT NOT NULL,
                revision INTEGER NOT NULL,
                last_retainer_stopped_at INTEGER,
                last_logged_out_at INTEGER,
                retainer_cooldown_minutes INTEGER,
                login_cooldown_minutes INTEGER,
                PRIMARY KEY (pool_id, customer_id),
                UNIQUE (pool_id, normalized_name)
            );
            CREATE TABLE IF NOT EXISTS customer_adjustments (
                pool_id TEXT NOT NULL,
                adjustment_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                action TEXT NOT NULL,
                seconds INTEGER NOT NULL,
                previous_balance INTEGER NOT NULL,
                resulting_balance INTEGER NOT NULL,
                created_at REAL NOT NULL,
                admin_name TEXT NOT NULL,
                PRIMARY KEY (pool_id, adjustment_id),
                UNIQUE (pool_id, operation_id)
            );
            CREATE TABLE IF NOT EXISTS customer_tombstones (
                pool_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                PRIMARY KEY (pool_id, customer_id)
            );
            CREATE TABLE IF NOT EXISTS customer_operations (
                pool_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                PRIMARY KEY (pool_id, operation_id)
            );
            CREATE TABLE IF NOT EXISTS customer_sync_acks (
                pool_id TEXT NOT NULL,
                pc_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                acknowledged_at REAL NOT NULL,
                PRIMARY KEY (pool_id, pc_id, customer_id)
            );
            CREATE TABLE IF NOT EXISTS customer_sync_state (
                pool_id TEXT NOT NULL,
                pc_id TEXT NOT NULL,
                last_error TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (pool_id, pc_id)
            );
            CREATE TABLE IF NOT EXISTS customer_ownership (
                pool_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                pc_id TEXT NOT NULL,
                login_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                logged_in_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                PRIMARY KEY (pool_id, customer_id),
                UNIQUE (pool_id, pc_id),
                UNIQUE (pool_id, login_id),
                UNIQUE (pool_id, operation_id)
            );
            CREATE TABLE IF NOT EXISTS retainer_sessions (
                pool_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                pc_id TEXT NOT NULL,
                login_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                state TEXT NOT NULL,
                balance_seconds INTEGER NOT NULL,
                prepared_at REAL NOT NULL,
                started_at REAL,
                checkpoint_at REAL,
                ended_at REAL,
                stop_reason TEXT,
                PRIMARY KEY (pool_id, session_id),
                UNIQUE (pool_id, operation_id)
            );
            CREATE TABLE IF NOT EXISTS wallpapers (
                pool_id TEXT PRIMARY KEY,
                wallpaper_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                content BLOB NOT NULL,
                saved_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_notifications (
                notification_id TEXT PRIMARY KEY,
                pool_id TEXT NOT NULL,
                pc_id TEXT NOT NULL,
                pc_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS customer_accounts_name "
            "ON customer_accounts(pool_id, normalized_name)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS customer_adjustments_customer "
            "ON customer_adjustments(pool_id, customer_id, created_at DESC)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS retainer_sessions_active "
            "ON retainer_sessions(pool_id, state, customer_id, pc_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS admin_notifications_pool_time "
            "ON admin_notifications(pool_id, created_at DESC)"
        )
        # Additive migration: keep existing identities, pools, and join history.
        join_columns = {row[1] for row in self.db.execute(
            "PRAGMA table_info(pending_joins)"
        )}
        for column, definition in (
            ("pairing_public", "TEXT"), ("pairing_admin_public", "TEXT"),
            ("pairing_secret", "TEXT"), ("expires_at", "REAL"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("verification_digest", "TEXT"), ("cancel_digest", "TEXT"),
        ):
            if column not in join_columns:
                self.db.execute(f"ALTER TABLE pending_joins ADD COLUMN {column} {definition}")
        customer_columns = {row[1] for row in self.db.execute(
            "PRAGMA table_info(customer_accounts)"
        )}
        for column, definition in (
            ("last_retainer_stopped_at", "INTEGER"),
            ("last_logged_out_at", "INTEGER"),
            ("retainer_cooldown_minutes", "INTEGER"),
            ("login_cooldown_minutes", "INTEGER"),
        ):
            if column not in customer_columns:
                self.db.execute(
                    f"ALTER TABLE customer_accounts ADD COLUMN {column} {definition}"
                )
        self.db.commit()
        self._migrate_local_owner_history()
        # Older snapshots may predate the bounded-history policy.
        for pool_id, snapshot_text in self.db.execute(
                "SELECT pool_id,snapshot FROM pools").fetchall():
            snapshot = json.loads(snapshot_text)
            original_snapshot = copy.deepcopy(snapshot)
            settings = snapshot.setdefault("settings", {})
            settings.setdefault("retainer_restart_cooldown_minutes", 0)
            settings.setdefault("customer_login_cooldown_minutes", 0)
            snapshot.setdefault("wallpaper", None)
            self.prune_history(snapshot)
            for (payload_text,) in self.db.execute(
                    "SELECT payload FROM owner_records WHERE pool_id=?", (pool_id,)):
                payload = json.loads(payload_text)
                snapshot["history"][payload["pc_id"]] = payload["history"][-HISTORY_PER_PC:]
            if snapshot != original_snapshot:
                with self.db:
                    self.db.execute("UPDATE pools SET snapshot=? WHERE pool_id=?",
                                    (json.dumps(snapshot, separators=(",", ":")), pool_id))
        if not self.local("pc_id"):
            self.set_local("pc_id", uuid.uuid4().hex)

    def _migrate_local_owner_history(self) -> None:
        """Only this PC can re-sign an oversized historical owner record."""
        own = self.local("pc_id")
        private = self.local("node_private_key")
        if not own or not private:
            return
        for pool_id, payload_text, signature in self.db.execute(
                "SELECT pool_id,payload,signature FROM owner_records WHERE pc_id=?",
                (own,)).fetchall():
            payload = json.loads(payload_text)
            if len(payload["history"]) <= HISTORY_PER_PC:
                continue
            row = self.db.execute("SELECT snapshot FROM pools WHERE pool_id=?",
                                  (pool_id,)).fetchone()
            if not row:
                continue
            snap = json.loads(row[0])
            public = snap["members"].get(own, {}).get("public_key")
            if public != node_public_from_private(private):
                continue
            verify_node(public, payload, signature)
            payload["version"] += 1
            payload["history"] = payload["history"][-HISTORY_PER_PC:]
            retained = {item["id"] for item in payload["history"]}
            if payload["session"]:
                retained.add(payload["session"]["id"])
            payload["grants"] = [item for item in payload["grants"]
                                 if item["command"]["session_id"] in retained]
            payload["legacy"] = {key: value for key, value in
                                 payload.get("legacy", {}).items() if key in retained}
            with self.db:
                self.db.execute(
                    "UPDATE owner_records SET version=?,payload=?,signature=? "
                    "WHERE pool_id=? AND pc_id=?",
                    (payload["version"], json.dumps(payload, separators=(",", ":")),
                     sign_node(private, payload), pool_id, own))

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

    def _local_customer_key(self) -> str:
        return f"customer_login:{self.current_pool_id or 'none'}"

    def local_customer_session(self) -> dict | None:
        value = self.local(self._local_customer_key())
        if not value:
            return None
        try:
            session = json.loads(value)
            return session if isinstance(session, dict) else None
        except (TypeError, ValueError):
            return None

    def save_local_customer_session(self, session: dict) -> None:
        self.set_local(self._local_customer_key(), json.dumps(
            session, separators=(",", ":")))

    def clear_local_customer_session(self) -> None:
        self.delete_local(self._local_customer_key())

    def _customer_pool(self) -> str:
        pool_id = self.current_pool_id
        if not pool_id:
            raise RuntimeError("This PC has not joined a cafe.")
        return pool_id

    @staticmethod
    def _customer_row(row) -> dict:
        return dict(zip((
            "customer_id", "name", "balance_seconds", "created_at",
            "modified_at", "modified_by", "revision",
            "last_retainer_stopped_at", "last_logged_out_at",
            "retainer_cooldown_minutes", "login_cooldown_minutes",
        ), row))

    def customer_accounts(self, search: str = "") -> list[dict]:
        """Return UI-safe account fields; password verifiers never leave storage."""
        pool_id = self._customer_pool()
        query = (
            "SELECT customer_id,name,balance_seconds,created_at,modified_at,"
            "modified_by,revision,last_retainer_stopped_at,last_logged_out_at,"
            "retainer_cooldown_minutes,login_cooldown_minutes "
            "FROM customer_accounts WHERE pool_id=?"
        )
        parameters: list[object] = [pool_id]
        if search.strip():
            _display, normalized = normalize_customer_name(search)
            query += " AND normalized_name LIKE ?"
            parameters.append(f"%{normalized}%")
        query += " ORDER BY normalized_name, customer_id"
        with self._lock:
            return [self._customer_row(row) for row in
                    self.db.execute(query, parameters).fetchall()]

    def customer_account(self, customer_id: str) -> dict | None:
        pool_id = self._customer_pool()
        with self._lock:
            row = self.db.execute(
                "SELECT customer_id,name,balance_seconds,created_at,modified_at,"
                "modified_by,revision,last_retainer_stopped_at,"
                "last_logged_out_at,retainer_cooldown_minutes,"
                "login_cooldown_minutes FROM customer_accounts "
                "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
            ).fetchone()
            return self._customer_row(row) if row else None

    def customer_cooldown_state(self, customer: str | dict,
                                now: int | float | None = None) -> dict:
        """Calculate both cooldowns from persisted UTC timestamps and live settings."""
        account = (self.customer_account(customer) if isinstance(customer, str)
                   else customer)
        if not account:
            raise ValueError("Customer no longer exists.")
        settings = self.snapshot()["settings"]
        current = int(time.time() if now is None else now)

        def calculate(kind: str, global_key: str) -> tuple[int, int, int]:
            override = account.get(f"{kind}_cooldown_minutes")
            minutes = (int(override) if override is not None else
                       int(settings.get(global_key, 0)))
            timestamp = account.get(
                "last_retainer_stopped_at" if kind == "retainer" else
                "last_logged_out_at")
            expires = int(timestamp) + minutes * 60 if timestamp else 0
            return minutes, expires, max(0, expires - current)

        retainer_minutes, retainer_expires, retainer_remaining = calculate(
            "retainer", "retainer_restart_cooldown_minutes")
        login_minutes, login_expires, login_remaining = calculate(
            "login", "customer_login_cooldown_minutes")
        return {
            "retainer_minutes": retainer_minutes,
            "retainer_expires_at": retainer_expires,
            "retainer_remaining": retainer_remaining,
            "login_minutes": login_minutes,
            "login_expires_at": login_expires,
            "login_remaining": login_remaining,
        }

    def customer_password_verifier(self, customer_id: str) -> dict | None:
        """Reserved for authoritative Stage 3 authentication."""
        pool_id = self._customer_pool()
        with self._lock:
            row = self.db.execute(
                "SELECT password_verifier FROM customer_accounts "
                "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
            ).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    @staticmethod
    def _ownership_row(row) -> dict:
        return dict(zip(("customer_id", "pc_id", "login_id", "operation_id",
                         "logged_in_at", "last_seen"), row))

    @staticmethod
    def _retainer_row(row) -> dict:
        return dict(zip((
            "session_id", "customer_id", "pc_id", "login_id",
            "operation_id", "state", "balance_seconds", "prepared_at",
            "started_at", "checkpoint_at", "ended_at", "stop_reason",
        ), row))

    def customer_ownership(self, customer_id: str = "",
                           pc_id: str = "") -> dict | None:
        pool_id = self._customer_pool()
        if not customer_id and not pc_id:
            raise ValueError("Customer ID or PC ID is required.")
        field, value = (("customer_id", customer_id) if customer_id
                        else ("pc_id", pc_id))
        with self._lock:
            row = self.db.execute(
                "SELECT customer_id,pc_id,login_id,operation_id,logged_in_at,"
                f"last_seen FROM customer_ownership WHERE pool_id=? AND {field}=?",
                (pool_id, value)
            ).fetchone()
            return self._ownership_row(row) if row else None

    def customer_ownerships(self) -> list[dict]:
        pool_id = self._customer_pool()
        with self._lock:
            rows = self.db.execute(
                "SELECT customer_id,pc_id,login_id,operation_id,logged_in_at,"
                "last_seen FROM customer_ownership WHERE pool_id=?",
                (pool_id,)
            ).fetchall()
            return [self._ownership_row(row) for row in rows]

    def authenticate_customer(self, customer_id: str, password: str,
                              pc_id: str, operation_id: str,
                              now: float | None = None) -> dict:
        """Authenticate on the authority and claim one customer for one PC."""
        if not operation_id or not pc_id:
            raise ValueError("Invalid customer login request.")
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            account = self.customer_account(customer_id)
            if not account:
                raise PermissionError("Customer name or password is incorrect.")
            cooldown = self.customer_cooldown_state(account, now)
            if cooldown["login_remaining"] > 0:
                raise RuntimeError(
                    "Login cooldown is active for "
                    f"{cooldown['login_remaining']} more seconds."
                )
            account_row = self.db.execute(
                "SELECT password_verifier FROM customer_accounts "
                "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
            ).fetchone()
            verifier = json.loads(account_row[0]) if account_row and account_row[0] else None
            if not verifier or not verify_password(password, verifier):
                raise PermissionError("Customer name or password is incorrect.")
            previous = self.db.execute(
                "SELECT customer_id,pc_id,login_id,operation_id,logged_in_at,"
                "last_seen FROM customer_ownership WHERE pool_id=? "
                "AND operation_id=?", (pool_id, operation_id)
            ).fetchone()
            if previous:
                ownership = self._ownership_row(previous)
                if (ownership["customer_id"] != customer_id
                        or ownership["pc_id"] != pc_id):
                    raise ValueError("Login request was already used.")
                return self.customer_login_state(
                    customer_id, pc_id, ownership["login_id"])
            occupied = self.db.execute(
                "SELECT customer_id,pc_id FROM customer_ownership "
                "WHERE pool_id=? AND (customer_id=? OR pc_id=?)",
                (pool_id, customer_id, pc_id)
            ).fetchone()
            if occupied:
                if occupied[0] == customer_id:
                    raise RuntimeError(
                        "This customer is already logged in on another PC."
                    )
                raise RuntimeError("This PC already has a registered customer login.")
            login_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO customer_ownership VALUES(?,?,?,?,?,?,?)",
                (pool_id, customer_id, pc_id, login_id, operation_id, now, now)
            )
            return self.customer_login_state(customer_id, pc_id, login_id)

    def customer_login_state(self, customer_id: str, pc_id: str,
                             login_id: str,
                             touch_at: float | None = None) -> dict:
        pool_id = self._customer_pool()
        with self._lock, self.db:
            ownership = self.db.execute(
                "SELECT customer_id,pc_id,login_id,operation_id,logged_in_at,"
                "last_seen FROM customer_ownership WHERE pool_id=? "
                "AND customer_id=? AND pc_id=? AND login_id=?",
                (pool_id, customer_id, pc_id, login_id)
            ).fetchone()
            if not ownership:
                raise PermissionError("This customer login is no longer active.")
            if touch_at is not None:
                self.db.execute(
                    "UPDATE customer_ownership SET last_seen=? WHERE pool_id=? "
                    "AND customer_id=?", (touch_at, pool_id, customer_id)
                )
            account = self.customer_account(customer_id)
            if not account:
                raise PermissionError("This customer account no longer exists.")
            retainer = self.retainer_for_login(customer_id, pc_id, login_id)
            return {"account": account,
                    "ownership": self._ownership_row(ownership),
                    "retainer": retainer,
                    "cooldowns": self.customer_cooldown_state(
                        account, touch_at if touch_at is not None else time.time())}

    def change_customer_password(self, customer_id: str, pc_id: str,
                                 login_id: str, current_password: str,
                                 new_password: str,
                                 now: float | None = None) -> dict:
        new_password = validate_customer_password(new_password)
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            self.customer_login_state(customer_id, pc_id, login_id)
            row = self.db.execute(
                "SELECT password_verifier,revision FROM customer_accounts "
                "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
            ).fetchone()
            verifier = json.loads(row[0]) if row and row[0] else None
            if not verifier or not verify_password(current_password, verifier):
                raise PermissionError("Current password is incorrect.")
            self.db.execute(
                "UPDATE customer_accounts SET password_verifier=?,modified_at=?,"
                "modified_by=?,revision=? WHERE pool_id=? AND customer_id=?",
                (json.dumps(hash_customer_password(new_password), separators=(",", ":")),
                 now, "Customer", int(row[1]) + 1, pool_id, customer_id)
            )
            return self.customer_account(customer_id)

    def release_customer_login(self, customer_id: str, pc_id: str,
                               login_id: str, force: bool = False,
                               reason: str = "logout",
                               record_logout: bool = False,
                               now: float | None = None) -> None:
        pool_id = self._customer_pool()
        now = int(time.time() if now is None else now)
        with self._lock, self.db:
            ownership = self.customer_ownership(customer_id=customer_id)
            if not ownership:
                # A retried logout is already complete and must not move the
                # cooldown timestamp forward.
                return
            self.customer_login_state(customer_id, pc_id, login_id)
            active = self.retainer_for_login(customer_id, pc_id, login_id)
            if active and active["state"] in ("prepared", "active"):
                if not force:
                    raise RuntimeError("Stop the active retainer session before logging out.")
                self.db.execute(
                    "UPDATE retainer_sessions SET state='stopped',ended_at=?,"
                    "stop_reason=? WHERE pool_id=? AND session_id=?",
                    (now, reason, pool_id, active["session_id"])
                )
            self.db.execute(
                "DELETE FROM customer_ownership WHERE pool_id=? "
                "AND customer_id=? AND pc_id=? AND login_id=?",
                (pool_id, customer_id, pc_id, login_id)
            )
            if record_logout:
                self.db.execute(
                    "UPDATE customer_accounts SET last_logged_out_at=?,"
                    "modified_at=?,modified_by='Customer logout',"
                    "revision=revision+1 WHERE pool_id=? AND customer_id=?",
                    (now, now, pool_id, customer_id)
                )

    def prepare_retainer(self, customer_id: str, pc_id: str,
                         login_id: str, operation_id: str,
                         now: float | None = None) -> dict:
        if not operation_id:
            raise ValueError("Retainer operation ID is required.")
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            state = self.customer_login_state(customer_id, pc_id, login_id,
                                              touch_at=now)
            account = state["account"]
            cooldown = self.customer_cooldown_state(account, now)
            if cooldown["retainer_remaining"] > 0:
                raise RuntimeError(
                    "Retainer restart cooldown is active for "
                    f"{cooldown['retainer_remaining']} more seconds."
                )
            previous = self.db.execute(
                "SELECT session_id,customer_id,pc_id,login_id,operation_id,state,"
                "balance_seconds,prepared_at,started_at,checkpoint_at,ended_at,"
                "stop_reason FROM retainer_sessions WHERE pool_id=? "
                "AND operation_id=?", (pool_id, operation_id)
            ).fetchone()
            if previous:
                retainer = self._retainer_row(previous)
                if (retainer["customer_id"] != customer_id
                        or retainer["pc_id"] != pc_id
                        or retainer["login_id"] != login_id):
                    raise ValueError("Retainer request was already used.")
                return retainer
            if account["balance_seconds"] <= 0:
                raise ValueError("This customer has no retainer time remaining.")
            active = self.db.execute(
                "SELECT 1 FROM retainer_sessions WHERE pool_id=? "
                "AND state IN ('prepared','active') "
                "AND (customer_id=? OR pc_id=?)",
                (pool_id, customer_id, pc_id)
            ).fetchone()
            if active:
                raise RuntimeError("A retainer session is already active.")
            session_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO retainer_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pool_id, session_id, customer_id, pc_id, login_id,
                 operation_id, "prepared", account["balance_seconds"], now,
                 None, None, None, None)
            )
            return self.retainer_session(session_id)

    def retainer_session(self, session_id: str) -> dict | None:
        pool_id = self._customer_pool()
        with self._lock:
            row = self.db.execute(
                "SELECT session_id,customer_id,pc_id,login_id,operation_id,state,"
                "balance_seconds,prepared_at,started_at,checkpoint_at,ended_at,"
                "stop_reason FROM retainer_sessions WHERE pool_id=? "
                "AND session_id=?", (pool_id, session_id)
            ).fetchone()
            return self._retainer_row(row) if row else None

    def retainer_for_login(self, customer_id: str, pc_id: str,
                           login_id: str) -> dict | None:
        pool_id = self._customer_pool()
        with self._lock:
            row = self.db.execute(
                "SELECT session_id,customer_id,pc_id,login_id,operation_id,state,"
                "balance_seconds,prepared_at,started_at,checkpoint_at,ended_at,"
                "stop_reason FROM retainer_sessions WHERE pool_id=? "
                "AND customer_id=? AND pc_id=? AND login_id=? "
                "ORDER BY prepared_at DESC LIMIT 1",
                (pool_id, customer_id, pc_id, login_id)
            ).fetchone()
            return self._retainer_row(row) if row else None

    def activate_retainer(self, session_id: str, customer_id: str,
                          pc_id: str, login_id: str,
                          now: float | None = None) -> dict:
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            state = self.customer_login_state(customer_id, pc_id, login_id,
                                              touch_at=now)
            cooldown = self.customer_cooldown_state(state["account"], now)
            if cooldown["retainer_remaining"] > 0:
                raise RuntimeError(
                    "Retainer restart cooldown is active for "
                    f"{cooldown['retainer_remaining']} more seconds."
                )
            retainer = self.retainer_session(session_id)
            if not retainer or any((retainer["customer_id"] != customer_id,
                                     retainer["pc_id"] != pc_id,
                                     retainer["login_id"] != login_id)):
                raise ValueError("Retainer session is no longer available.")
            if retainer["state"] == "active":
                return retainer
            if retainer["state"] != "prepared":
                raise RuntimeError("Retainer session has already ended.")
            self.db.execute(
                "UPDATE retainer_sessions SET state='active',started_at=?,"
                "checkpoint_at=? WHERE pool_id=? AND session_id=?",
                (now, now, pool_id, session_id)
            )
            return self.retainer_session(session_id)

    def active_retainers(self) -> list[dict]:
        pool_id = self._customer_pool()
        with self._lock:
            rows = self.db.execute(
                "SELECT session_id,customer_id,pc_id,login_id,operation_id,state,"
                "balance_seconds,prepared_at,started_at,checkpoint_at,ended_at,"
                "stop_reason FROM retainer_sessions WHERE pool_id=? "
                "AND state='active'", (pool_id,)
            ).fetchall()
            return [self._retainer_row(row) for row in rows]

    def customer_retainer_in_progress(self, customer_id: str) -> bool:
        """Prepared and active retainers both reserve the account balance."""
        pool_id = self._customer_pool()
        with self._lock:
            return self.db.execute(
                "SELECT 1 FROM retainer_sessions WHERE pool_id=? AND "
                "customer_id=? AND state IN ('prepared','active')",
                (pool_id, customer_id)
            ).fetchone() is not None

    def consume_retainer(self, session_id: str, seconds: int,
                         finalize: bool = False, reason: str = "stopped",
                         now: float | None = None) -> dict:
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
            raise ValueError("Retainer elapsed time is invalid.")
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            retainer = self.retainer_session(session_id)
            if not retainer or retainer["state"] != "active":
                raise RuntimeError("Retainer session is not active.")
            account = self.customer_account(retainer["customer_id"])
            if not account:
                raise RuntimeError("Customer account no longer exists.")
            deduction = min(seconds, account["balance_seconds"])
            balance = account["balance_seconds"] - deduction
            stopped = finalize or balance == 0
            record_stop = stopped and (reason == "customer" or balance == 0)
            if deduction or record_stop:
                revision = account["revision"] + 1
                self.db.execute(
                    "UPDATE customer_accounts SET balance_seconds=?,modified_at=?,"
                    "modified_by='Retainer gameplay',revision=?,"
                    "last_retainer_stopped_at=CASE WHEN ? THEN ? ELSE "
                    "last_retainer_stopped_at END WHERE pool_id=? "
                    "AND customer_id=? AND revision=?",
                    (balance, now, revision, int(record_stop), int(now),
                     pool_id, account["customer_id"], account["revision"])
                )
            self.db.execute(
                "UPDATE retainer_sessions SET balance_seconds=?,checkpoint_at=?,"
                "state=?,ended_at=?,stop_reason=? WHERE pool_id=? AND session_id=?",
                (balance, now, "stopped" if stopped else "active",
                 now if stopped else None,
                 ("exhausted" if balance == 0 else reason) if stopped else None,
                 pool_id, session_id)
            )
            return {"account": self.customer_account(account["customer_id"]),
                    "retainer": self.retainer_session(session_id),
                    "exhausted": balance == 0, "deducted": deduction}

    def abort_retainer(self, session_id: str, pc_id: str,
                       login_id: str,
                       reason: str = "unlock_failed") -> None:
        pool_id, now = self._customer_pool(), time.time()
        with self._lock, self.db:
            self.db.execute(
                "UPDATE retainer_sessions SET state='stopped',ended_at=?,"
                "stop_reason=? WHERE pool_id=? AND session_id=? AND pc_id=? "
                "AND login_id=? AND state IN ('prepared','active')",
                (now, reason, pool_id, session_id, pc_id, login_id)
            )

    def recover_interrupted_retainers(self,
                                      now: float | None = None) -> list[dict]:
        """Keep the last checkpoint, charge no downtime, and require a new login."""
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            rows = self.db.execute(
                "SELECT session_id,customer_id,pc_id,login_id,operation_id,state,"
                "balance_seconds,prepared_at,started_at,checkpoint_at,ended_at,"
                "stop_reason FROM retainer_sessions WHERE pool_id=? "
                "AND state IN ('prepared','active')", (pool_id,)
            ).fetchall()
            retainers = [self._retainer_row(row) for row in rows]
            for retainer in retainers:
                self.db.execute(
                    "UPDATE retainer_sessions SET state='stopped',ended_at=?,"
                    "stop_reason='interrupted' WHERE pool_id=? AND session_id=?",
                    (now, pool_id, retainer["session_id"])
                )
                self.db.execute(
                    "DELETE FROM customer_ownership WHERE pool_id=? "
                    "AND customer_id=? AND login_id=?",
                    (pool_id, retainer["customer_id"], retainer["login_id"])
                )
            return retainers

    def force_release_customer(self, customer_id: str,
                               reason: str = "admin_release") -> dict | None:
        ownership = self.customer_ownership(customer_id=customer_id)
        if not ownership:
            return None
        self.release_customer_login(customer_id, ownership["pc_id"],
                                    ownership["login_id"], force=True,
                                    reason=reason)
        return ownership

    def create_customer(self, name: str, admin_name: str,
                        operation_id: str, now: float | None = None) -> dict:
        display, normalized = normalize_customer_name(name)
        if not operation_id:
            raise ValueError("Customer operation ID is required.")
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            prior = self.db.execute(
                "SELECT customer_id FROM customer_operations "
                "WHERE pool_id=? AND operation_id=?", (pool_id, operation_id)
            ).fetchone()
            if prior:
                account = self.customer_account(prior[0])
                if account:
                    if normalize_customer_name(account["name"])[1] != normalized:
                        raise ValueError(
                            "Operation ID was already used for another customer."
                        )
                    return account
                raise RuntimeError("This customer operation was already completed.")
            if self.db.execute(
                    "SELECT 1 FROM customer_accounts WHERE pool_id=? "
                    "AND normalized_name=?", (pool_id, normalized)).fetchone():
                raise ValueError("A customer with this name already exists.")
            customer_id = uuid.uuid4().hex
            verifier = json.dumps(hash_customer_password(), separators=(",", ":"))
            try:
                self.db.execute(
                    "INSERT INTO customer_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pool_id, customer_id, display, normalized, verifier, 0,
                     now, now, admin_name, 1, None, None, None, None)
                )
                self.db.execute(
                    "INSERT INTO customer_operations VALUES(?,?,?,?,?)",
                    (pool_id, operation_id, "create", customer_id, 1)
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("A customer with this name already exists.") from error
            return self.customer_account(customer_id)

    def set_customer_cooldown_overrides(
            self, customer_id: str, retainer_minutes: int | None,
            login_minutes: int | None, expected_revision: int,
            admin_name: str, now: float | None = None) -> dict:
        """Save nullable per-customer overrides; zero explicitly disables."""
        if retainer_minutes is not None:
            retainer_minutes = validate_cooldown_minutes(retainer_minutes)
        if login_minutes is not None:
            login_minutes = validate_cooldown_minutes(login_minutes)
        pool_id = self._customer_pool()
        now = time.time() if now is None else now
        with self._lock, self.db:
            updated = self.db.execute(
                "UPDATE customer_accounts SET retainer_cooldown_minutes=?,"
                "login_cooldown_minutes=?,modified_at=?,modified_by=?,"
                "revision=revision+1 WHERE pool_id=? AND customer_id=? "
                "AND revision=?",
                (retainer_minutes, login_minutes, now, admin_name, pool_id,
                 customer_id, expected_revision)
            )
            if updated.rowcount != 1:
                raise RuntimeError(
                    "Customer changed. Review the refreshed account and try again."
                )
            return self.customer_account(customer_id)

    def clear_customer_cooldown(self, customer_id: str, kind: str,
                                admin_name: str,
                                now: float | None = None) -> dict:
        if kind not in ("retainer", "login"):
            raise ValueError("Choose a valid customer cooldown.")
        pool_id = self._customer_pool()
        now = time.time() if now is None else now
        column = ("last_retainer_stopped_at" if kind == "retainer"
                  else "last_logged_out_at")
        with self._lock, self.db:
            updated = self.db.execute(
                f"UPDATE customer_accounts SET {column}=NULL,modified_at=?,"
                "modified_by=?,revision=revision+1 WHERE pool_id=? "
                "AND customer_id=?",
                (now, admin_name, pool_id, customer_id)
            )
            if updated.rowcount != 1:
                raise ValueError("Customer no longer exists.")
            return self.customer_account(customer_id)

    def adjust_customer_balance(self, customer_id: str, action: str,
                                seconds: int, expected_revision: int,
                                expected_balance: int, admin_name: str,
                                operation_id: str,
                                now: float | None = None) -> dict:
        if action not in ("add", "reduce"):
            raise ValueError("Choose Add or Reduce.")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or \
                not 0 < seconds <= MAX_RETAINER_SECONDS:
            raise ValueError("Retainer adjustment is invalid or too large.")
        if not operation_id:
            raise ValueError("Customer operation ID is required.")
        pool_id, now = self._customer_pool(), time.time() if now is None else now
        with self._lock, self.db:
            prior = self.db.execute(
                "SELECT action,customer_id,seconds FROM customer_adjustments "
                "WHERE pool_id=? AND operation_id=?", (pool_id, operation_id)
            ).fetchone()
            if prior:
                if (prior[0] != action or prior[1] != customer_id
                        or int(prior[2]) != seconds):
                    raise ValueError("Operation ID was already used for another change.")
                account = self.customer_account(customer_id)
                if not account:
                    raise ValueError("Customer no longer exists.")
                return account
            account = self.customer_account(customer_id)
            if not account:
                raise ValueError("Customer no longer exists.")
            if self.db.execute(
                    "SELECT 1 FROM retainer_sessions WHERE pool_id=? AND "
                    "customer_id=? AND state IN ('prepared','active')",
                    (pool_id, customer_id)).fetchone():
                raise RuntimeError(
                    "Balance adjustments are unavailable during an active "
                    "retainer session. End the session before making changes."
                )
            if (account["revision"] != expected_revision or
                    account["balance_seconds"] != expected_balance):
                raise RuntimeError(
                    "Customer balance changed. Review the refreshed balance and confirm again."
                )
            resulting = (expected_balance + seconds if action == "add"
                         else expected_balance - seconds)
            if resulting < 0:
                raise ValueError("Retainer balance cannot be reduced below zero.")
            revision = expected_revision + 1
            adjustment_id = uuid.uuid4().hex
            updated = self.db.execute(
                "UPDATE customer_accounts SET balance_seconds=?,modified_at=?,"
                "modified_by=?,revision=? WHERE pool_id=? AND customer_id=? "
                "AND revision=?",
                (resulting, now, admin_name, revision, pool_id, customer_id,
                 expected_revision)
            )
            if updated.rowcount != 1:
                raise RuntimeError("Customer balance changed. Refresh and try again.")
            self.db.execute(
                "INSERT INTO customer_adjustments VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pool_id, adjustment_id, operation_id, customer_id, action,
                 seconds, expected_balance, resulting, now, admin_name)
            )
            self.db.execute(
                "INSERT INTO customer_operations VALUES(?,?,?,?,?)",
                (pool_id, operation_id, action, customer_id, revision)
            )
            return self.customer_account(customer_id)

    def customer_adjustments(self, customer_id: str,
                             limit: int = 100) -> list[dict]:
        pool_id = self._customer_pool()
        with self._lock:
            rows = self.db.execute(
                "SELECT adjustment_id,action,seconds,previous_balance,"
                "resulting_balance,created_at,admin_name "
                "FROM customer_adjustments WHERE pool_id=? AND customer_id=? "
                "ORDER BY created_at DESC,adjustment_id DESC LIMIT ?",
                (pool_id, customer_id, max(1, min(int(limit), 500)))
            ).fetchall()
            fields = ("adjustment_id", "action", "seconds", "previous_balance",
                      "resulting_balance", "created_at", "admin_name")
            return [dict(zip(fields, row)) for row in rows]

    def delete_customer(self, customer_id: str, exact_name: str,
                        expected_revision: int, operation_id: str) -> int:
        pool_id = self._customer_pool()
        with self._lock, self.db:
            if self.db.execute(
                    "SELECT 1 FROM customer_ownership WHERE pool_id=? "
                    "AND customer_id=?", (pool_id, customer_id)).fetchone():
                raise RuntimeError(
                    "Log out this customer and end any active session before deletion."
                )
            prior = self.db.execute(
                "SELECT kind,customer_id,revision FROM customer_operations "
                "WHERE pool_id=? AND operation_id=?", (pool_id, operation_id)
            ).fetchone()
            if prior:
                if prior[0] != "delete" or prior[1] != customer_id:
                    raise ValueError("Operation ID was already used for another change.")
                return int(prior[2])
            account = self.customer_account(customer_id)
            if not account:
                raise ValueError("Customer no longer exists.")
            if exact_name != account["name"]:
                raise ValueError("Enter the customer's exact displayed name.")
            if account["revision"] != expected_revision:
                raise RuntimeError("Customer changed. Review the account and confirm again.")
            revision = expected_revision + 1
            self.db.execute(
                "DELETE FROM customer_adjustments WHERE pool_id=? AND customer_id=?",
                (pool_id, customer_id)
            )
            self.db.execute(
                "DELETE FROM customer_accounts WHERE pool_id=? AND customer_id=?",
                (pool_id, customer_id)
            )
            self.db.execute(
                "DELETE FROM customer_operations WHERE pool_id=? AND customer_id=?",
                (pool_id, customer_id)
            )
            self.db.execute(
                "INSERT INTO customer_tombstones VALUES(?,?,?) "
                "ON CONFLICT(pool_id,customer_id) DO UPDATE SET "
                "revision=MAX(revision,excluded.revision)",
                (pool_id, customer_id, revision)
            )
            self.db.execute(
                "INSERT INTO customer_operations VALUES(?,?,?,?,?)",
                (pool_id, operation_id, "delete", customer_id, revision)
            )
            return revision

    def customer_replica_payload(self) -> dict:
        pool_id = self._customer_pool()
        with self._lock:
            accounts = [{
                "customer_id": row[0], "name": row[1],
                "balance_seconds": row[2], "created_at": row[3],
                "modified_at": row[4], "modified_by": row[5],
                "revision": row[6],
                "last_retainer_stopped_at": row[7],
                "last_logged_out_at": row[8],
                "retainer_cooldown_minutes": row[9],
                "login_cooldown_minutes": row[10],
            } for row in self.db.execute(
                "SELECT customer_id,name,balance_seconds,created_at,modified_at,"
                "modified_by,revision,last_retainer_stopped_at,"
                "last_logged_out_at,retainer_cooldown_minutes,"
                "login_cooldown_minutes FROM customer_accounts WHERE pool_id=?",
                (pool_id,)
            )]
            tombstones = [{"customer_id": row[0], "revision": row[1]}
                          for row in self.db.execute(
                              "SELECT customer_id,revision FROM customer_tombstones "
                              "WHERE pool_id=?", (pool_id,))]
            return {"accounts": accounts, "tombstones": tombstones}

    def apply_customer_replica(self, accounts: list[dict],
                               tombstones: list[dict]) -> dict[str, int]:
        """Durably apply monotonic Admin replicas and return saved revisions."""
        pool_id = self._customer_pool()
        acknowledgments: dict[str, int] = {}
        with self._lock, self.db:
            for item in tombstones:
                customer_id, revision = item["customer_id"], int(item["revision"])
                previous = self.db.execute(
                    "SELECT revision FROM customer_tombstones "
                    "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
                ).fetchone()
                saved_revision = max(revision, int(previous[0]) if previous else 0)
                self.db.execute(
                    "INSERT INTO customer_tombstones VALUES(?,?,?) "
                    "ON CONFLICT(pool_id,customer_id) DO UPDATE SET "
                    "revision=MAX(revision,excluded.revision)",
                    (pool_id, customer_id, saved_revision)
                )
                account = self.db.execute(
                    "SELECT revision FROM customer_accounts "
                    "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
                ).fetchone()
                if not account or int(account[0]) <= saved_revision:
                    self.db.execute(
                        "DELETE FROM customer_adjustments WHERE pool_id=? AND customer_id=?",
                        (pool_id, customer_id)
                    )
                    self.db.execute(
                        "DELETE FROM customer_accounts WHERE pool_id=? AND customer_id=?",
                        (pool_id, customer_id)
                    )
                acknowledgments[customer_id] = saved_revision
            for item in accounts:
                customer_id, revision = item["customer_id"], int(item["revision"])
                tombstone = self.db.execute(
                    "SELECT revision FROM customer_tombstones "
                    "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
                ).fetchone()
                if tombstone:
                    acknowledgments[customer_id] = int(tombstone[0])
                    continue
                current = self.db.execute(
                    "SELECT revision FROM customer_accounts "
                    "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
                ).fetchone()
                if not current or revision > int(current[0]):
                    display, normalized = normalize_customer_name(item["name"])
                    balance = int(item["balance_seconds"])
                    if balance < 0:
                        raise ValueError("Replicated retainer balance is invalid.")
                    self.db.execute(
                        "INSERT INTO customer_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(pool_id,customer_id) DO UPDATE SET "
                        "name=excluded.name,normalized_name=excluded.normalized_name,"
                        "password_verifier=customer_accounts.password_verifier,"
                        "balance_seconds=excluded.balance_seconds,"
                        "created_at=excluded.created_at,modified_at=excluded.modified_at,"
                        "modified_by=excluded.modified_by,revision=excluded.revision,"
                        "last_retainer_stopped_at=excluded.last_retainer_stopped_at,"
                        "last_logged_out_at=excluded.last_logged_out_at,"
                        "retainer_cooldown_minutes=excluded.retainer_cooldown_minutes,"
                        "login_cooldown_minutes=excluded.login_cooldown_minutes",
                        (pool_id, customer_id, display, normalized, None, balance,
                         float(item["created_at"]), float(item["modified_at"]),
                         str(item.get("modified_by", "Admin")), revision,
                         item.get("last_retainer_stopped_at"),
                         item.get("last_logged_out_at"),
                         item.get("retainer_cooldown_minutes"),
                         item.get("login_cooldown_minutes"))
                    )
                saved = self.db.execute(
                    "SELECT revision FROM customer_accounts "
                    "WHERE pool_id=? AND customer_id=?", (pool_id, customer_id)
                ).fetchone()
                acknowledgments[customer_id] = int(saved[0])
        return acknowledgments

    def note_customer_sync(self, pc_id: str, acknowledgments: dict[str, int],
                           error: str | None = None) -> None:
        pool_id, now = self._customer_pool(), time.time()
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO customer_sync_state VALUES(?,?,?,?) "
                "ON CONFLICT(pool_id,pc_id) DO UPDATE SET "
                "last_error=excluded.last_error,updated_at=excluded.updated_at",
                (pool_id, pc_id, error, now)
            )
            for customer_id, revision in acknowledgments.items():
                self.db.execute(
                    "INSERT INTO customer_sync_acks VALUES(?,?,?,?,?) "
                    "ON CONFLICT(pool_id,pc_id,customer_id) DO UPDATE SET "
                    "revision=MAX(revision,excluded.revision),"
                    "acknowledged_at=excluded.acknowledged_at",
                    (pool_id, pc_id, customer_id, int(revision), now)
                )

    def customer_sync_records(self, customer_id: str) -> dict[str, dict]:
        pool_id = self._customer_pool()
        with self._lock:
            rows = self.db.execute(
                "SELECT a.pc_id,a.revision,a.acknowledged_at,s.last_error "
                "FROM customer_sync_acks a LEFT JOIN customer_sync_state s "
                "ON s.pool_id=a.pool_id AND s.pc_id=a.pc_id "
                "WHERE a.pool_id=? AND a.customer_id=?", (pool_id, customer_id)
            ).fetchall()
            result = {row[0]: {"revision": row[1], "acknowledged_at": row[2],
                               "error": row[3]} for row in rows}
            for pc_id, error, updated in self.db.execute(
                    "SELECT pc_id,last_error,updated_at FROM customer_sync_state "
                    "WHERE pool_id=?", (pool_id,)).fetchall():
                result.setdefault(pc_id, {"revision": 0,
                                          "acknowledged_at": 0})
                result[pc_id]["error"] = error
                result[pc_id]["updated_at"] = updated
            return result

    @staticmethod
    def _handoff_digest(state: dict) -> str:
        encoded = json.dumps(
            state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _authoritative_customer_state(self, snapshot: dict | None = None) -> dict:
        """Return only shared authority data; machine-local identity stays local."""
        pool_id = self._customer_pool()

        def rows(query: str, fields: tuple[str, ...]) -> list[dict]:
            return [dict(zip(fields, row)) for row in self.db.execute(
                query, (pool_id,)).fetchall()]

        account_fields = (
            "customer_id", "name", "normalized_name", "password_verifier",
            "balance_seconds", "created_at", "modified_at", "modified_by",
            "revision", "last_retainer_stopped_at", "last_logged_out_at",
            "retainer_cooldown_minutes", "login_cooldown_minutes",
        )
        accounts = rows(
            "SELECT customer_id,name,normalized_name,password_verifier,"
            "balance_seconds,created_at,modified_at,modified_by,revision,"
            "last_retainer_stopped_at,last_logged_out_at,"
            "retainer_cooldown_minutes,login_cooldown_minutes "
            "FROM customer_accounts WHERE pool_id=? ORDER BY customer_id",
            account_fields)
        for account in accounts:
            raw = account["password_verifier"]
            account["password_verifier"] = json.loads(raw) if raw else None
        return {
            "snapshot": copy.deepcopy(snapshot or self.snapshot()),
            "accounts": accounts,
            "adjustments": rows(
                "SELECT adjustment_id,operation_id,customer_id,action,seconds,"
                "previous_balance,resulting_balance,created_at,admin_name "
                "FROM customer_adjustments WHERE pool_id=? ORDER BY adjustment_id",
                ("adjustment_id", "operation_id", "customer_id", "action",
                 "seconds", "previous_balance", "resulting_balance",
                 "created_at", "admin_name")),
            "tombstones": rows(
                "SELECT customer_id,revision FROM customer_tombstones "
                "WHERE pool_id=? ORDER BY customer_id",
                ("customer_id", "revision")),
            "operations": rows(
                "SELECT operation_id,kind,customer_id,revision "
                "FROM customer_operations WHERE pool_id=? ORDER BY operation_id",
                ("operation_id", "kind", "customer_id", "revision")),
            "ownership": rows(
                "SELECT customer_id,pc_id,login_id,operation_id,logged_in_at,"
                "last_seen FROM customer_ownership WHERE pool_id=? "
                "ORDER BY customer_id",
                ("customer_id", "pc_id", "login_id", "operation_id",
                 "logged_in_at", "last_seen")),
            "retainers": rows(
                "SELECT session_id,customer_id,pc_id,login_id,operation_id,state,"
                "balance_seconds,prepared_at,started_at,checkpoint_at,ended_at,"
                "stop_reason FROM retainer_sessions WHERE pool_id=? "
                "ORDER BY session_id",
                ("session_id", "customer_id", "pc_id", "login_id",
                 "operation_id", "state", "balance_seconds", "prepared_at",
                 "started_at", "checkpoint_at", "ended_at", "stop_reason")),
        }

    def authoritative_handoff_bundle(self, source_admin: str, candidate: str,
                                     term: int, transfer_id: str) -> dict:
        """Capture a consistent, complete Admin-only customer snapshot."""
        with self._lock:
            state = self._authoritative_customer_state()
            bundle = {
                "version": 1, "pool_id": self._customer_pool(),
                "source_admin": source_admin, "candidate": candidate,
                "term": int(term), "transfer_id": transfer_id,
                "state": state, "state_digest": self._handoff_digest(state),
            }
            self.validate_authoritative_handoff_bundle(
                bundle, source_admin, candidate, term, transfer_id)
            return bundle

    def validate_authoritative_handoff_bundle(
            self, bundle: dict, source_admin: str, candidate: str,
            term: int, transfer_id: str) -> None:
        """Reject incomplete credentials, inconsistent records, and stale context."""
        if (not isinstance(bundle, dict) or bundle.get("version") != 1
                or bundle.get("pool_id") != self._customer_pool()
                or bundle.get("source_admin") != source_admin
                or bundle.get("candidate") != candidate
                or int(bundle.get("term", -1)) != int(term)
                or bundle.get("transfer_id") != transfer_id):
            raise ValueError("Admin handoff context is invalid.")
        state = bundle.get("state")
        required = {"snapshot", "accounts", "adjustments", "tombstones",
                    "operations", "ownership", "retainers"}
        if (not isinstance(state, dict) or set(state) != required
                or bundle.get("state_digest") != self._handoff_digest(state)):
            raise ValueError("Admin handoff state is incomplete or corrupt.")
        snapshot = state["snapshot"]
        if (not isinstance(snapshot, dict)
                or snapshot.get("pool_id") != self._customer_pool()
                or snapshot.get("active_admin", {}).get("pc_id") != source_admin
                or candidate not in snapshot.get("members", {})):
            raise ValueError("Admin handoff snapshot is invalid.")
        for key in required - {"snapshot"}:
            if not isinstance(state[key], list) or len(state[key]) > 100_000:
                raise ValueError("Admin handoff record set is invalid or too large.")
        if state["ownership"]:
            raise RuntimeError(
                "Registered customer ownership must end before Admin transfer.")

        account_ids: set[str] = set()
        names: set[str] = set()
        for account in state["accounts"]:
            try:
                if not isinstance(account, dict):
                    raise TypeError
                customer_id = str(account["customer_id"])
                display, normalized = normalize_customer_name(account["name"])
                verifier = account["password_verifier"]
                if (not isinstance(verifier, dict)
                        or set(verifier) != {"salt", "hash", "iterations"}):
                    raise TypeError
                salt = bytes.fromhex(verifier["salt"])
                digest = bytes.fromhex(verifier["hash"])
                iterations = int(verifier["iterations"])
                balance = int(account["balance_seconds"])
                revision = int(account["revision"])
                float(account["created_at"])
                float(account["modified_at"])
                str(account["modified_by"])
                for timestamp in (account["last_retainer_stopped_at"],
                                  account["last_logged_out_at"]):
                    if timestamp is not None:
                        int(timestamp)
                for cooldown in (account["retainer_cooldown_minutes"],
                                 account["login_cooldown_minutes"]):
                    if cooldown is not None:
                        validate_cooldown_minutes(cooldown)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    "Admin handoff contains an invalid customer account.") from error
            if (not customer_id or customer_id in account_ids
                    or display != account["name"]
                    or normalized != account["normalized_name"]
                    or normalized in names or len(salt) != 16 or len(digest) != 32
                    or iterations <= 0 or balance < 0 or revision < 1):
                raise ValueError(
                    "Admin handoff contains an invalid customer account.")
            account_ids.add(customer_id)
            names.add(normalized)
        try:
            tombstones = {
                str(item["customer_id"]) for item in state["tombstones"]
                if isinstance(item, dict) and int(item["revision"]) >= 1
            }
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Admin handoff tombstones are inconsistent.") from error
        if ("" in tombstones or account_ids & tombstones
                or len(tombstones) != len(state["tombstones"])):
            raise ValueError("Admin handoff tombstones are inconsistent.")
        known_ids = account_ids | tombstones
        adjustment_ids: set[str] = set()
        adjustment_operations: dict[str, tuple[str, str, int]] = {}
        for item in state["adjustments"]:
            try:
                adjustment_id = str(item["adjustment_id"])
                operation_id = str(item["operation_id"])
                customer_id = str(item["customer_id"])
                action = str(item["action"])
                seconds = int(item["seconds"])
                previous = int(item["previous_balance"])
                resulting = int(item["resulting_balance"])
                float(item["created_at"])
                str(item["admin_name"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    "Admin handoff adjustment history is invalid.") from error
            expected = (previous + seconds if action == "add"
                        else previous - seconds)
            if (not adjustment_id or adjustment_id in adjustment_ids
                    or not operation_id
                    or operation_id in adjustment_operations
                    or customer_id not in account_ids
                    or action not in ("add", "reduce")
                    or not 0 < seconds <= MAX_RETAINER_SECONDS
                    or previous < 0 or resulting < 0 or resulting != expected):
                raise ValueError("Admin handoff adjustment history is invalid.")
            adjustment_ids.add(adjustment_id)
            adjustment_operations[operation_id] = (
                action, customer_id, resulting)
        operation_ids: set[str] = set()
        create_ids: set[str] = set()
        delete_ids: set[str] = set()
        for item in state["operations"]:
            try:
                operation_id = str(item["operation_id"])
                kind = str(item["kind"])
                customer_id = str(item["customer_id"])
                revision = int(item["revision"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    "Admin handoff operation history is invalid.") from error
            if (not operation_id or operation_id in operation_ids
                    or customer_id not in known_ids or revision < 1
                    or kind not in ("create", "add", "reduce", "delete")):
                raise ValueError("Admin handoff operation history is invalid.")
            if kind in ("add", "reduce"):
                adjustment = adjustment_operations.get(operation_id)
                if (not adjustment or adjustment[0] != kind
                        or adjustment[1] != customer_id):
                    raise ValueError(
                        "Admin handoff operation history is incomplete.")
            elif kind == "create":
                create_ids.add(customer_id)
            else:
                delete_ids.add(customer_id)
            operation_ids.add(operation_id)
        if (set(adjustment_operations) - operation_ids
                or account_ids - create_ids or tombstones - delete_ids):
            raise ValueError("Admin handoff operation history is incomplete.")
        retainer_ids: set[str] = set()
        retainer_operations: set[str] = set()
        for item in state["retainers"]:
            try:
                session_id = str(item["session_id"])
                operation_id = str(item["operation_id"])
                customer_id = str(item["customer_id"])
                balance = int(item["balance_seconds"])
                float(item["prepared_at"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    "Admin handoff retainer history is invalid.") from error
            if (not session_id or session_id in retainer_ids
                    or not operation_id or operation_id in retainer_operations
                    or customer_id not in known_ids
                    or item.get("state") != "stopped" or balance < 0):
                raise ValueError("Admin handoff retainer history is not finalized.")
            retainer_ids.add(session_id)
            retainer_operations.add(operation_id)

    def apply_authoritative_handoff_bundle(
            self, bundle: dict, source_admin: str, candidate: str,
            term: int, transfer_id: str) -> str:
        """Atomically replace shared authority tables after full validation."""
        self.validate_authoritative_handoff_bundle(
            bundle, source_admin, candidate, term, transfer_id)
        state, pool_id = bundle["state"], self._customer_pool()
        with self._lock, self.db:
            for table in ("customer_adjustments", "customer_operations",
                          "customer_ownership", "retainer_sessions",
                          "customer_accounts", "customer_tombstones",
                          "customer_sync_acks", "customer_sync_state"):
                self.db.execute(f"DELETE FROM {table} WHERE pool_id=?", (pool_id,))
            for item in state["accounts"]:
                self.db.execute(
                    "INSERT INTO customer_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pool_id, item["customer_id"], item["name"],
                     item["normalized_name"], json.dumps(
                         item["password_verifier"], separators=(",", ":")),
                     int(item["balance_seconds"]), float(item["created_at"]),
                     float(item["modified_at"]), str(item["modified_by"]),
                     int(item["revision"]), item["last_retainer_stopped_at"],
                     item["last_logged_out_at"],
                     item["retainer_cooldown_minutes"],
                     item["login_cooldown_minutes"]))
            for item in state["adjustments"]:
                self.db.execute(
                    "INSERT INTO customer_adjustments VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (pool_id, item["adjustment_id"], item["operation_id"],
                     item["customer_id"], item["action"], int(item["seconds"]),
                     int(item["previous_balance"]),
                     int(item["resulting_balance"]), float(item["created_at"]),
                     str(item["admin_name"])))
            for item in state["tombstones"]:
                self.db.execute(
                    "INSERT INTO customer_tombstones VALUES(?,?,?)",
                    (pool_id, item["customer_id"], int(item["revision"])))
            for item in state["operations"]:
                self.db.execute(
                    "INSERT INTO customer_operations VALUES(?,?,?,?,?)",
                    (pool_id, item["operation_id"], item["kind"],
                     item["customer_id"], int(item["revision"])))
            for item in state["retainers"]:
                self.db.execute(
                    "INSERT INTO retainer_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pool_id, item["session_id"], item["customer_id"],
                     item["pc_id"], item["login_id"], item["operation_id"],
                     item["state"], int(item["balance_seconds"]),
                     float(item["prepared_at"]), item["started_at"],
                     item["checkpoint_at"], item["ended_at"],
                     item["stop_reason"]))
            snapshot = copy.deepcopy(state["snapshot"])
            self.db.execute(
                "UPDATE pools SET revision=?,snapshot=? WHERE pool_id=?",
                (int(snapshot["revision"]), json.dumps(
                    snapshot, separators=(",", ":")), pool_id))
            saved = self._authoritative_customer_state(snapshot)
            saved_digest = self._handoff_digest(saved)
            if saved_digest != bundle["state_digest"]:
                raise RuntimeError(
                    "Durable Admin handoff verification did not match the source.")
        return saved_digest

    @staticmethod
    def _validated_wallpaper(content: bytes, media_type: str) -> tuple[str, str]:
        if not isinstance(content, bytes) or not content:
            raise ValueError("Wallpaper image data is required.")
        if len(content) > MAX_WALLPAPER_BYTES:
            raise ValueError("Wallpaper exceeds the 500 KB transfer limit.")
        detected = (
            "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else
            "image/jpeg" if content.startswith(b"\xff\xd8\xff") else ""
        )
        if media_type not in ("image/png", "image/jpeg") or detected != media_type:
            raise ValueError("Wallpaper must contain valid PNG or JPEG data.")
        return hashlib.sha256(content).hexdigest(), detected

    def set_wallpaper(self, content: bytes, media_type: str,
                      now: int | None = None) -> dict:
        """Atomically save managed image data and its replicated metadata."""
        wallpaper_id, media_type = self._validated_wallpaper(content, media_type)
        pool_id = self._customer_pool()
        now = int(time.time() if now is None else now)
        with self._lock, self.db:
            snapshot = self.snapshot()
            previous = snapshot.get("wallpaper") or {}
            if previous.get("id") == wallpaper_id:
                metadata = previous
            else:
                metadata = {"id": wallpaper_id,
                            "revision": int(previous.get("revision", 0)) + 1,
                            "media_type": media_type}
                snapshot["wallpaper"] = metadata
                snapshot["revision"] += 1
            self.db.execute(
                "INSERT INTO wallpapers VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(pool_id) DO UPDATE SET "
                "wallpaper_id=excluded.wallpaper_id,revision=excluded.revision,"
                "media_type=excluded.media_type,content=excluded.content,"
                "saved_at=excluded.saved_at",
                (pool_id, wallpaper_id, int(metadata["revision"]), media_type,
                 content, now)
            )
            self.db.execute(
                "UPDATE pools SET revision=?,snapshot=? WHERE pool_id=?",
                (snapshot["revision"], json.dumps(snapshot, separators=(",", ":")),
                 pool_id)
            )
            return metadata

    def reset_wallpaper(self) -> None:
        pool_id = self._customer_pool()
        with self._lock, self.db:
            snapshot = self.snapshot()
            if snapshot.get("wallpaper") is not None:
                snapshot["wallpaper"] = None
                snapshot["revision"] += 1
                self.db.execute(
                    "UPDATE pools SET revision=?,snapshot=? WHERE pool_id=?",
                    (snapshot["revision"],
                     json.dumps(snapshot, separators=(",", ":")), pool_id)
                )
            self.db.execute("DELETE FROM wallpapers WHERE pool_id=?", (pool_id,))

    def wallpaper_content(self) -> tuple[dict, bytes] | None:
        snapshot = self.snapshot()
        metadata = (snapshot or {}).get("wallpaper")
        if not metadata:
            return None
        with self._lock:
            row = self.db.execute(
                "SELECT wallpaper_id,revision,media_type,content FROM wallpapers "
                "WHERE pool_id=?", (snapshot["pool_id"],)
            ).fetchone()
        if not row or row[0] != metadata.get("id"):
            return None
        content = bytes(row[3])
        try:
            wallpaper_id, media_type = self._validated_wallpaper(content, row[2])
        except ValueError:
            return None
        if wallpaper_id != metadata.get("id"):
            return None
        return ({"id": wallpaper_id, "revision": int(row[1]),
                 "media_type": media_type}, content)

    def current_wallpaper_id(self) -> str:
        current = self.wallpaper_content()
        return current[0]["id"] if current else ""

    def apply_wallpaper(self, metadata: dict, content: bytes,
                        now: int | None = None) -> str:
        snapshot = self.snapshot()
        expected = (snapshot or {}).get("wallpaper") or {}
        wallpaper_id, media_type = self._validated_wallpaper(
            content, str(metadata.get("media_type", "")))
        if (wallpaper_id != metadata.get("id")
                or wallpaper_id != expected.get("id")
                or int(metadata.get("revision", -1)) !=
                int(expected.get("revision", -2))):
            raise PermissionError("Wallpaper does not match authoritative metadata.")
        pool_id = self._customer_pool()
        now = int(time.time() if now is None else now)
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO wallpapers VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(pool_id) DO UPDATE SET "
                "wallpaper_id=excluded.wallpaper_id,revision=excluded.revision,"
                "media_type=excluded.media_type,content=excluded.content,"
                "saved_at=excluded.saved_at",
                (pool_id, wallpaper_id, int(metadata["revision"]), media_type,
                 content, now)
            )
        return wallpaper_id

    def post_locked_notice(self, title: str, message: str) -> None:
        self.set_local("locked_console_notice", json.dumps({
            "id": uuid.uuid4().hex, "title": title, "message": message,
            "created_at": int(time.time()),
        }, separators=(",", ":")))

    def take_locked_notice(self) -> dict | None:
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT value FROM local WHERE key='locked_console_notice'"
            ).fetchone()
            if not row:
                return None
            self.db.execute(
                "DELETE FROM local WHERE key='locked_console_notice'"
            )
        try:
            notice = json.loads(row[0])
            return notice if isinstance(notice, dict) else None
        except (TypeError, ValueError):
            return None

    def add_notification(self, pc_id: str, pc_name: str, event_type: str,
                         created_at: float | None = None) -> dict:
        """Persist one Admin-local lifecycle event and retain only the newest 100."""
        pool_id = self.current_pool_id
        if not pool_id:
            raise RuntimeError("This PC has not joined a cafe.")
        if event_type not in ("online", "offline", "closed"):
            raise ValueError("Unsupported notification event type.")
        row = {
            "notification_id": uuid.uuid4().hex,
            "pool_id": pool_id,
            "pc_id": pc_id,
            "pc_name": str(pc_name),
            "event_type": event_type,
            "created_at": int(time.time() if created_at is None else created_at),
            "acknowledged": False,
        }
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO admin_notifications"
                "(notification_id,pool_id,pc_id,pc_name,event_type,created_at,acknowledged) "
                "VALUES(?,?,?,?,?,?,0)",
                (row["notification_id"], pool_id, pc_id, row["pc_name"],
                 event_type, row["created_at"]),
            )
            self.db.execute(
                "DELETE FROM admin_notifications WHERE pool_id=? AND notification_id NOT IN ("
                "SELECT notification_id FROM admin_notifications WHERE pool_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                (pool_id, pool_id, NOTIFICATION_LIMIT),
            )
        return row

    def notifications(self) -> list[dict]:
        pool_id = self.current_pool_id
        if not pool_id:
            return []
        with self._lock:
            rows = self.db.execute(
                "SELECT notification_id,pc_id,pc_name,event_type,created_at,acknowledged "
                "FROM admin_notifications WHERE pool_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (pool_id, NOTIFICATION_LIMIT),
            ).fetchall()
        keys = ("notification_id", "pc_id", "pc_name", "event_type",
                "created_at", "acknowledged")
        return [{**dict(zip(keys, row)), "acknowledged": bool(row[5])}
                for row in rows]

    def acknowledge_notifications(self) -> None:
        pool_id = self.current_pool_id
        if not pool_id:
            return
        with self._lock, self.db:
            self.db.execute(
                "UPDATE admin_notifications SET acknowledged=1 "
                "WHERE pool_id=? AND acknowledged=0", (pool_id,)
            )

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
            self.prune_history(result)
            for (payload_text,) in self.db.execute(
                    "SELECT payload FROM owner_records WHERE pool_id=?", (pool_id,)):
                payload = json.loads(payload_text)
                owner = payload["pc_id"]
                result["sessions"].pop(owner, None)
                if payload["session"] is not None:
                    result["sessions"][owner] = payload["session"]
                # The signed owner version is authoritative; snapshot unions revive deletions.
                result["history"][owner] = payload["history"][-HISTORY_PER_PC:]
            return result

    def owner_records(self) -> list[dict]:
        with self._lock:
            return [{"payload": json.loads(row[0]), "signature": row[1]}
                    for row in self.db.execute(
                        "SELECT payload, signature FROM owner_records WHERE pool_id=?",
                        (self.current_pool_id,))]

    def save_owner_record(self, session: dict | None, history: list[dict],
                          grants: list[dict], run_id: str,
                          activity_at: float,
                          clear_command: dict | None = None) -> dict:
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
                       "grants": grants, "legacy": legacy,
                       "history_epoch": (previous.get("history_epoch", 0) if previous else 0)
                                        + (1 if clear_command else 0),
                       "history_clear": clear_command if clear_command else
                                        (previous.get("history_clear") if previous else None)}
            signature = sign_node(self.node_private_key(), payload)
            self.db.execute(
                "INSERT INTO owner_records VALUES(?,?,?,?,?) ON CONFLICT(pool_id,pc_id) "
                "DO UPDATE SET version=excluded.version,payload=excluded.payload,"
                "signature=excluded.signature",
                (snap["pool_id"], owner, payload["version"],
                 json.dumps(payload, separators=(",", ":")), signature))
            self._sync_snapshot_history(snap["pool_id"], owner, retained)
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
                old_epoch = int(old.get("history_epoch", 0))
                new_epoch = int(payload.get("history_epoch", 0))
                if new_epoch not in (old_epoch, old_epoch + 1):
                    raise PermissionError("Invalid history generation.")
                if new_epoch == old_epoch and payload.get("history_clear") != old.get("history_clear"):
                    raise PermissionError("History clear proof changed unexpectedly.")
                old_active = old["session"]
                new_active = payload["session"]
                new_history = {item["id"]: item for item in payload["history"]}
                if old_active:
                    if new_active and new_active["id"] == old_active["id"]:
                        if new_active["paid_minutes"] < old_active["paid_minutes"]:
                            raise PermissionError("Paid allocation cannot shrink.")
                    elif old_active["id"] not in new_history:
                        raise PermissionError("A completed session needs a history record.")
                if new_epoch == old_epoch:
                    old_ids = [item["id"] for item in old["history"]]
                    new_ids = [item["id"] for item in payload["history"]]
                    fresh = [item for item in new_ids if item not in old_ids]
                    expected = (old_ids + fresh)[-HISTORY_PER_PC:]
                    if new_ids != expected:
                        raise PermissionError("Owner history is not FIFO append-only.")
                elif payload["history"]:
                    raise PermissionError("A clear record must start with empty history.")
            elif int(payload.get("history_epoch", 0)) < 0:
                raise PermissionError("Invalid history generation.")
            history_ids = [item["id"] for item in payload["history"]]
            if len(history_ids) > HISTORY_PER_PC:
                raise PermissionError("Owner history exceeds the retention limit.")
            if len(history_ids) != len(set(history_ids)):
                raise PermissionError("Duplicate session history ID.")
            if payload["session"] and payload["session"]["id"] in history_ids:
                raise PermissionError("A completed session cannot reopen.")
            clear = payload.get("history_clear")
            if int(payload.get("history_epoch", 0)) > 0 and not clear:
                raise PermissionError("History clear lacks an Admin command.")
            if clear:
                command = clear["command"]
                if (command["action"] != "clear_history" or command["target"] != owner
                        or command["pool_id"] != snap["pool_id"]):
                    raise PermissionError("Invalid history clear target.")
                verify_node(snap["members"][command["admin_id"]]["public_key"],
                            command, clear["signature"])
                if not any(self._grant_proof_valid(key, command, clear)
                           for key in snap.get("admin_signing_keys",
                                               [snap["admin_signing"]["public"]])):
                    raise PermissionError("History clear has no Admin proof.")
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
            self._sync_snapshot_history(snap["pool_id"], owner, payload["history"])
            return True

    def _sync_snapshot_history(self, pool_id: str, owner: str,
                               history: list[dict]) -> None:
        """Remove pruned/cleared copies from the replicated pool blob too."""
        row = self.db.execute("SELECT snapshot FROM pools WHERE pool_id=?",
                              (pool_id,)).fetchone()
        raw = json.loads(row[0])
        raw["history"][owner] = history[-HISTORY_PER_PC:]
        self.db.execute("UPDATE pools SET snapshot=? WHERE pool_id=?",
                        (json.dumps(raw, separators=(",", ":")), pool_id))

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
            snapshot = copy.deepcopy(snapshot)
            settings = snapshot.setdefault("settings", {})
            settings.setdefault("retainer_restart_cooldown_minutes", 0)
            settings.setdefault("customer_login_cooldown_minutes", 0)
            snapshot.setdefault("wallpaper", None)
            self.prune_history(snapshot)
            for (payload_text,) in self.db.execute(
                    "SELECT payload FROM owner_records WHERE pool_id=?",
                    (snapshot["pool_id"],)):
                payload = json.loads(payload_text)
                snapshot["history"][payload["pc_id"]] = payload["history"][-HISTORY_PER_PC:]
            self.db.execute(
                "INSERT INTO pools(pool_id,revision,snapshot,secret) VALUES(?,?,?,?) "
                "ON CONFLICT(pool_id) DO UPDATE SET revision=excluded.revision, "
                "snapshot=excluded.snapshot, secret=excluded.secret",
                (snapshot["pool_id"], snapshot["revision"],
                 json.dumps(snapshot, separators=(",", ":")), secret),
            )
            if snapshot.get("wallpaper") is None:
                self.db.execute(
                    "DELETE FROM wallpapers WHERE pool_id=?",
                    (snapshot["pool_id"],)
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
                    ip: str, public_key: str, pairing_public: str,
                    pairing_admin_public: str, pairing_secret: str,
                    verification_digest: str, cancel_digest: str) -> None:
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
                "pairing_public,pairing_admin_public,pairing_secret,expires_at,"
                "verification_digest,cancel_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, pc_id, name, ip, public_key, now,
                 pairing_public, pairing_admin_public, pairing_secret, now + 300,
                 verification_digest, cancel_digest)
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

    def verify_join_code(self, request_id: str, pc_id: str,
                         entered: str) -> str:
        error = None
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT decision,expires_at,attempts,pairing_secret,"
                "verification_digest "
                "FROM pending_joins WHERE request_id=? AND pc_id=?",
                (request_id, pc_id)
            ).fetchone()
            if not row or row[0] != "pending" or row[1] <= time.time():
                raise RuntimeError("Join request expired; generate a new code.")
            if not row[3] or not row[4]:
                raise RuntimeError("Pairing request is incomplete.")
            if row[2] >= 5:
                raise PermissionError("Too many incorrect codes; request a new code.")
            entered_digest = hmac.new(
                bytes.fromhex(row[3]),
                f"verify:{request_id}:{entered}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(entered_digest, row[4]):
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

    def cancel_join(self, request_id: str, pc_id: str,
                    cancel_token: str) -> bool:
        """Cancel only the request carrying its high-entropy requester token."""
        with self._lock, self.db:
            row = self.db.execute(
                "SELECT decision,expires_at,pairing_secret,cancel_digest "
                "FROM pending_joins WHERE request_id=? AND pc_id=?",
                (request_id, pc_id),
            ).fetchone()
            if not row or row[0] != "pending" or row[1] <= time.time():
                return False
            digest = hmac.new(
                bytes.fromhex(row[2]),
                f"cancel:{request_id}:{cancel_token}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(digest, row[3] or ""):
                raise PermissionError("Invalid pairing cancellation.")
            self.db.execute(
                "UPDATE pending_joins SET decision='cancelled' WHERE request_id=?",
                (request_id,),
            )
            return True

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

    def take_local_commands(self, limit: int | None = None) -> list[tuple[str, dict]]:
        with self._lock, self.db:
            query = "SELECT id,action,payload FROM local_commands ORDER BY id"
            rows = self.db.execute(
                query + " LIMIT ?" if limit is not None else query,
                (limit,) if limit is not None else (),
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
