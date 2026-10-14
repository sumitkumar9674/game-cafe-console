"""Pool authority, session writes, replication, and Client control."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import hashlib
import hmac
import json
import queue
import re
import secrets
import threading
import time
import uuid

from cryptography.exceptions import InvalidSignature

from . import sessions
from .customers import retainer_seconds, validate_cooldown_minutes
from .desktops import active_desktop_name, switch
from .diagnostics import (fingerprint, info as diagnostic_info,
                          unexpected as diagnostic_unexpected,
                          warning as diagnostic_warning)
from .lifecycle import lifecycle_event
from .network import (NodeNetwork, authenticated_call, create_pairing_key,
                      discover, open_welcome, pairing_secret,
                      plain_call, seal_welcome)
from .presentation import validate_add, validate_start
from .security import (create_admin_signing_record, sign_admin_proof,
                       create_handoff_keypair, open_admin_handoff,
                       seal_admin_handoff, sign_node, unlock_admin_signing_key,
                       verify_admin_proof, verify_node,
                       verify_pairing_revocation)
from .storage import (AUTHORITY_PROTOCOL, Store, hash_password,
                      verify_password)


LEASE_SECONDS = 15
AUTHORITY_FRESH_SECONDS = 30
PEER_ONLINE_SECONDS = 9
SYNC_INTERVAL_SECONDS = 3
RETAINER_CHECKPOINT_SECONDS = 10
CUSTOMER_AUTH_ATTEMPTS = 5
CUSTOMER_AUTH_BLOCK_SECONDS = 30
HANDOFF_EXPIRY_SECONDS = 60
HANDOFF_CHUNK_HEX_LENGTH = 300_000
MAX_HANDOFF_PLAINTEXT = 8_000_000
PAIRING_REMOVAL_SECONDS = 20


def customer_controls(snapshot: dict, pc_id: str) -> dict[str, bool]:
    """Only User stations expose session controls in the Admin dashboard."""
    if pc_id not in snapshot["members"] or \
            pc_id == snapshot["active_admin"].get("pc_id"):
        return {"start": False, "add": False, "end": False}
    session = snapshot["sessions"].get(pc_id)
    return {"start": session is None,
            "add": bool(session and session["kind"] == "timed"),
            "end": True}


def admin_rank(snapshot: dict) -> tuple[int, str]:
    owner = snapshot["active_admin"]
    return int(owner.get("term", 0)), owner.get("pc_id") or ""


class Runtime:
    """One controller instance for one PC and one current cafe pool."""

    def __init__(self, store: Store):
        self.store = store
        self.started_at = time.time()
        self.network = NodeNetwork(store, self)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.default_handle: int | None = None
        self.console_handle: int | None = None
        self.console_available = False
        self.admin_ui_open = False
        self.last_access_allowed: bool | None = None
        self.lock = threading.RLock()
        self.peer_statuses: dict[str, tuple[float, dict]] = {}
        self.connection_states: dict[str, bool] = {}
        self.peer_failures: dict[str, int] = {}
        self.graceful_disconnects: set[str] = set()
        self.admin_signing_key = None
        self.lease_challenges: dict[tuple[str, str], float] = {}
        self.handoff_transfer: dict | None = None
        self.run_id = uuid.uuid4().hex
        self.last_checkpoint = 0.0
        self.local_shutdown_pending = False
        self.retainer_clocks: dict[str, tuple[float, float]] = {}
        self.customer_auth_failures: dict[tuple[str, str], tuple[int, float]] = {}
        self.retainer_unlock_pending = False
        self.pairing_removal: dict | None = None
        self.handoff_deadline = 0.0
        self.local_claim_deadline = 0.0
        self.admin_claim_receipts: dict[tuple[str, int], float] = {}
        self.pending_retry_state: dict[str, tuple[float, int]] = {}
        self.customer_sync_errors: dict[str, str] = {}
        snapshot = self.store.snapshot()
        diagnostic_info(
            "APPLICATION", "RUNTIME_INITIALIZED",
            pc=fingerprint(self.store.pc_id),
            pool=fingerprint(self.store.current_pool_id),
            role=("designated-admin" if snapshot and
                  snapshot["active_admin"].get("pc_id") == self.store.pc_id
                  else "user"),
        )

    def start(self) -> None:
        self.network.start()
        try:
            # The bound LAN port excludes a second parent process on this PC.
            if self.store.current_pool_id:
                self.store.set_local(self._lease_key("until"), "0")
                self._resume_designated_admin()
                # A process restart always requires a fresh customer login.
                self.store.clear_local_customer_session()
                if self.is_admin():
                    self.store.recover_interrupted_retainers()
            self.recover_interrupted_session()
            self._migrate_legacy_owner_session()
        except Exception:
            self.network.stop()
            raise
        self.worker = threading.Thread(target=self._background_loop,
                                       name="cafe-sync", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        diagnostic_info("APPLICATION", "RUNTIME_STOPPING",
                        pc=fingerprint(self.store.pc_id))
        self.stop_event.set()
        lifecycle_event("network_listener_shutdown_started")
        self.network.stop()
        lifecycle_event("network_listener_shutdown_completed")
        if self.worker:
            lifecycle_event("runtime_background_worker_join_started",
                            worker_alive=self.worker.is_alive())
            self.worker.join()
            lifecycle_event("runtime_background_worker_join_completed")
            self.worker = None
        diagnostic_info("APPLICATION", "RUNTIME_STOPPED",
                        pc=fingerprint(self.store.pc_id))

    def release_admin(self) -> None:
        was_admin = self.is_admin()
        if self.store.current_pool_id and self.is_admin():
            self.store.set_local(self._lease_key("until"), "0")
        self.admin_signing_key = None
        self.local_claim_deadline = 0.0
        if was_admin:
            diagnostic_info("AUTHORITY", "LOCAL_AUTHORITY_RELEASED",
                            pc=fingerprint(self.store.pc_id))

    def _owner_payload(self) -> tuple[dict | None, list[dict], list[dict]]:
        snap = self.snapshot()
        own = self.store.pc_id
        record = next((item["payload"] for item in self.store.owner_records()
                       if item["payload"]["pc_id"] == own), None)
        return (snap["sessions"].get(own),
                list(snap["history"].get(own, [])),
                list(record["grants"]) if record else [])

    def _save_owner_state(self, session: dict | None, history: list[dict],
                          grants: list[dict], now: float | None = None,
                          clear_command: dict | None = None) -> dict:
        record = self.store.save_owner_record(
            session, history, grants, self.run_id,
            time.time() if now is None else now, clear_command)
        if session:
            self.last_checkpoint = time.time() if now is None else now
        self.notify("changed")
        return record

    def recover_interrupted_session(self) -> bool:
        """A previous-run checkpoint, not network reachability, ends a crashed run."""
        if not self.store.current_pool_id:
            return False
        checkpoint = self.store.checkpoint()
        if not checkpoint or checkpoint["run_id"] == self.run_id:
            return False
        session, history, grants = self._owner_payload()
        if not session or session["id"] != checkpoint["session_id"]:
            self.store.clear_checkpoint()
            return False
        at = max(session["started_at"], checkpoint["activity_at"])
        # Use the last saved state/time; never charge the powered-off interval.
        recovered = checkpoint["session"]
        record = sessions.finish_session(
            recovered, self.store.pc_id,
            self.snapshot()["members"][self.store.pc_id]["name"],
            "interrupted", at)
        if not any(item["id"] == record["id"] for item in history):
            history.append(record)
        self._save_owner_state(None, history, grants)
        diagnostic_info(
            "SESSION", "INTERRUPTED_SESSION_RECOVERED",
            pc=fingerprint(self.store.pc_id),
            session=fingerprint(record["id"]),
            duration_seconds=record.get("counted_seconds", 0),
            result="persisted")
        return True

    def _migrate_legacy_owner_session(self) -> None:
        """Preserve an existing pre-checkpoint session on first V2 launch."""
        if not self.store.current_pool_id:
            return
        if any(item["payload"]["pc_id"] == self.store.pc_id
               for item in self.store.owner_records()):
            return
        session = self.snapshot()["sessions"].get(self.store.pc_id)
        if session:
            _, history, _ = self._owner_payload()
            self._save_owner_state(session, history, [])

    def checkpoint_local_session(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if now - self.last_checkpoint < 60:
            return
        session = self.snapshot()["sessions"].get(self.store.pc_id)
        if session:
            self.store.save_checkpoint(self.run_id, session, now)
        self.last_checkpoint = now

    def finish_local_session(self, reason: str = "customer",
                             reconcile_access: bool = True) -> bool:
        with self.lock:
            if self.local_shutdown_pending and reason != "close_software":
                raise RuntimeError("This PC is closing; session changes are unavailable.")
            session, history, grants = self._owner_payload()
            if not session:
                return False
            snap = self.snapshot()
            now = time.time()
            if session["kind"] == "timed":
                sessions.enter_grace_if_due(session, now,
                                            snap["settings"]["grace_minutes"])
            record = sessions.finish_session(
                session, self.store.pc_id,
                snap["members"][self.store.pc_id]["name"], reason, now)
            if not any(item["id"] == record["id"] for item in history):
                history.append(record)
            self._save_owner_state(None, history, grants, now)
            diagnostic_info(
                "SESSION", "SESSION_COMPLETED",
                pc=fingerprint(self.store.pc_id),
                session=fingerprint(record["id"]), reason=reason,
                duration_seconds=record.get("counted_seconds", 0),
                result="persisted")
            if reconcile_access:
                self._reconcile_local_access()
            return True

    def prepare_local_shutdown(self) -> bool:
        """Durably finalize only this PC before any desktop or process teardown."""
        with self.lock:
            if not self.store.current_pool_id:
                return False
            if self.local_shutdown_pending:
                return False
            had_session = False
            if self.is_admin():
                self.prepare_admin_shutdown()
            local_customer = self.store.local_customer_session()
            if local_customer:
                if local_customer.get("retainer_active"):
                    try:
                        self.stop_local_retainer()
                    except Exception:
                        pass
                if self.snapshot()["sessions"].get(self.store.pc_id):
                    had_session = self.finish_local_session(
                        "close_software", reconcile_access=False)
                try:
                    self.logout_customer()
                except Exception:
                    self.store.clear_local_customer_session()
            self.local_shutdown_pending = True
            try:
                had_session = (self.finish_local_session(
                    "close_software", reconcile_access=False) or had_session)
                self._announce_graceful_shutdown()
                return had_session
            except Exception:
                self.local_shutdown_pending = False
                raise

    def _announce_graceful_shutdown(self) -> None:
        """Best-effort authenticated notice; shutdown must still work offline."""
        if self.is_admin():
            return
        ip = self.active_admin_ip()
        if not ip:
            return
        try:
            self.network.call(ip, "graceful_shutdown", {})
        except Exception:
            pass

    def prepare_admin_shutdown(self) -> None:
        """Checkpoint retainers, then tell registered PCs to lock and log out."""
        if not self.is_admin():
            return
        self._checkpoint_active_retainers(force=True)
        for ownership in list(self.store.customer_ownerships()):
            retainer = self.store.retainer_for_login(
                ownership["customer_id"], ownership["pc_id"],
                ownership["login_id"])
            if retainer and retainer["state"] == "active":
                try:
                    self._stop_authoritative_retainer(
                        retainer["session_id"], ownership["customer_id"],
                        ownership["pc_id"], ownership["login_id"],
                        "admin_shutdown")
                except Exception:
                    pass
                retainer = self.store.retainer_for_login(
                    ownership["customer_id"], ownership["pc_id"],
                    ownership["login_id"])
            account = self.store.customer_account(ownership["customer_id"])
            self._force_retainer_target(retainer or {
                **ownership,
                "balance_seconds": account["balance_seconds"] if account else 0,
            }, logout=True, reason="admin_shutdown")
            self.store.force_release_customer(
                ownership["customer_id"], "admin_shutdown")

    def flush_owner_record(self) -> None:
        own_record = next((item for item in self.store.owner_records()
                           if item["payload"]["pc_id"] == self.store.pc_id), None)
        if not own_record:
            return
        peers = [item for item in self.store.peers()
                 if item["pc_id"] in self.snapshot()["members"]]
        if not peers:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as executor:
            futures = [executor.submit(self.network.call, peer["ip"],
                                       "session_record", {"record": own_record})
                       for peer in peers]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass  # Durable local record will be retried after reconnect.

    def notify(self, kind: str, value: object = None) -> None:
        self.events.put((kind, value))

    def snapshot(self) -> dict | None:
        return self.store.snapshot()

    def _lease_key(self, field: str) -> str:
        return f"lease_{field}:{self.store.current_pool_id}"

    def _resume_designated_admin(self) -> None:
        """Restore only this PC's signed designation, never elect a new owner."""
        with self.lock:
            snap = self.snapshot()
            if not snap or snap["active_admin"].get("pc_id") != self.store.pc_id:
                return
            claim = snap["active_admin"]
            term = int(claim.get("term", 0))
            if int(self.store.local(self._lease_key("handed_off")) or 0) >= term:
                return
            proof = {"pool_id": snap["pool_id"], "candidate": self.store.pc_id,
                     "term": term}
            signature = claim.get("proof") or ""
            trusted_keys = {snap["admin_signing"]["public"],
                            *snap.get("admin_signing_keys", [])}
            for public_key in trusted_keys:
                try:
                    verify_admin_proof(public_key, proof, signature)
                    break
                except (ValueError, InvalidSignature):
                    continue
            else:
                return  # An unsigned or invalid claim needs a password-backed login.
            self._hold_local_claim(term)

            def extend(state):
                active = state["active_admin"]
                if active.get("pc_id") != self.store.pc_id or active.get("term") != term:
                    raise RuntimeError("Admin designation changed during startup.")
                active["expires_at"] = time.time() + LEASE_SECONDS

            self.store.update(extend)

    def _claim_key(self, pc_id: str, term: int) -> tuple[str, int]:
        return str(pc_id), int(term)

    def _note_admin_claim(self, pc_id: str, term: int) -> None:
        """Start a recipient-local freshness window after authenticated receipt."""
        key = self._claim_key(pc_id, term)
        previous = self.admin_claim_receipts.get(key)
        self.admin_claim_receipts = {key: time.monotonic()}
        if previous is None or time.monotonic() - previous > AUTHORITY_FRESH_SECONDS:
            diagnostic_info("AUTHORITY", "CLAIM_ACCEPTED",
                            pc=fingerprint(self.store.pc_id),
                            admin=fingerprint(pc_id), term=term,
                            freshness_seconds=AUTHORITY_FRESH_SECONDS)

    def _admin_claim_fresh(self, pc_id: str, term: int) -> bool:
        received = self.admin_claim_receipts.get(self._claim_key(pc_id, term))
        return bool(received is not None and
                    time.monotonic() - received <= AUTHORITY_FRESH_SECONDS)

    def _require_remote_admin(self, sender: str, term: int | None = None) -> dict:
        """Validate authority using term plus this recipient's monotonic receipt."""
        snap = self.snapshot()
        claim = snap["active_admin"]
        expected_term = int(claim.get("term", 0))
        if (sender != claim.get("pc_id")
                or (term is not None and int(term) != expected_term)
                or not self._admin_claim_fresh(sender, expected_term)):
            diagnostic_warning(
                "AUTHORITY", "CLAIM_REJECTED", pc=fingerprint(self.store.pc_id),
                admin=fingerprint(sender), expected_admin=fingerprint(
                    claim.get("pc_id")), term=term, expected_term=expected_term,
                reason="recipient-local authority freshness missing or expired",
            )
            raise PermissionError(
                "Current Admin authority has not been refreshed on this PC.")
        return claim

    def is_admin(self) -> bool:
        snap = self.snapshot()
        if not snap:
            return False
        lease = snap["active_admin"]
        return (time.monotonic() >= self.handoff_deadline
                and lease.get("pc_id") == self.store.pc_id
                and int(lease.get("term", 0)) == int(
                    self.store.local(self._lease_key("term")) or 0)
                and self.local_claim_deadline > time.monotonic()
                and self.store.local(self._lease_key("pc")) == self.store.pc_id
                and self.store.local(self._lease_key("run")) == self.run_id)

    def active_admin_ip(self) -> str | None:
        snap = self.snapshot()
        if not snap:
            return None
        admin_id = snap["active_admin"].get("pc_id")
        if admin_id == self.store.pc_id and self.is_admin():
            return "127.0.0.1"
        for peer in self.store.peers():
            if peer["pc_id"] == admin_id:
                return peer["ip"]
        return None

    def status(self) -> dict:
        snap = self.snapshot()
        if not snap:
            return {"registered": False}
        own = self.store.pc_id
        session = snap["sessions"].get(own)
        grace = snap["settings"]["grace_minutes"]
        customer = self.store.local_customer_session()
        return {
            "registered": True, "pc_id": own,
            "pc_name": snap["members"].get(own, {}).get("name", "PC"),
            "revision": snap["revision"],
            "desktop": active_desktop_name(),
            "phase": sessions.phase(session, grace_minutes=grace),
            "session": session,
            "console_available": self.console_available,
            "access_ready": self._local_access_ready(),
            "admin": self.is_admin(),
            "admin_term": int(snap["active_admin"].get("term", 0)),
            "authority_protocol": AUTHORITY_PROTOCOL,
            "admin_control_ready": bool(
                self.is_admin() or self._admin_claim_fresh(
                    str(snap["active_admin"].get("pc_id", "")),
                    int(snap["active_admin"].get("term", 0)))),
            "registered_customer_id": (customer or {}).get("customer_id", ""),
            "registered_customer_name": (customer or {}).get("name", ""),
            "retainer_active": bool((customer or {}).get("retainer_active")),
            "retainer_remaining": self._local_retainer_remaining(customer),
        }

    @staticmethod
    def _local_retainer_remaining(customer: dict | None) -> int:
        if not customer:
            return 0
        balance = max(0, int(customer.get("balance_seconds", 0)))
        if not customer.get("retainer_active"):
            return balance
        displayed_at = float(customer.get("displayed_at", time.time()))
        return max(0, balance - int(max(0, time.time() - displayed_at)))

    def peer_status(self, pc_id: str) -> dict | None:
        with self.lock:
            item = self.peer_statuses.get(pc_id)
            if not item or time.time() - item[0] > PEER_ONLINE_SECONDS:
                return None
            return item[1]

    def verified_admin_online(self) -> bool:
        """Find a live Admin without depending on the last saved owner."""
        return self._find_live_admin() is not None

    def confirmed_remote_admin_online(self) -> bool:
        """Use the verified claim and cached heartbeat for login-screen state."""
        snap = self.snapshot()
        if not snap:
            return False
        claim = snap["active_admin"]
        admin_id = claim.get("pc_id")
        if (not admin_id or admin_id == self.store.pc_id
                or admin_id not in snap["members"]):
            return False
        status = self.peer_status(admin_id)
        return bool(status and status.get("admin")
                    and int(status.get("authority_protocol", 0))
                    == AUTHORITY_PROTOCOL
                    and status.get("pc_id") == admin_id
                    and int(status.get("admin_term", -1))
                    == int(claim.get("term", 0)))

    def _find_live_admin(self) -> tuple[str, str] | None:
        if not self.snapshot():
            return None
        self._discover_peers_once()
        self._poll_peer_statuses()
        peers = {peer["pc_id"]: peer["ip"] for peer in self.store.peers()}
        live = [(pc_id, status) for pc_id in peers
                if pc_id != self.store.pc_id
                and (status := self.peer_status(pc_id))
                and status.get("admin") and status.get("pc_id") == pc_id]
        live.sort(key=lambda item: (int(item[1].get("admin_term", 0)), item[0]),
                  reverse=True)
        for pc_id, _ in live:
            try:
                latest = self.network.call(peers[pc_id], "snapshot", {})["snapshot"]
                if (latest["active_admin"].get("pc_id") == pc_id
                        and int(latest.get("authority_protocol", 0))
                        == AUTHORITY_PROTOCOL):
                    self.accept_admin_snapshot(latest, pc_id,
                                               follow_live_admin=True)
                    return pc_id, peers[pc_id]
            except Exception:
                continue
        return None

    def accept_admin_snapshot(self, incoming: dict, sender: str,
                              follow_live_admin: bool = False) -> bool:
        """Check a new owner's password-backed claim before saving its state."""
        current = self.snapshot()
        if int(incoming.get("authority_protocol", 0)) != AUTHORITY_PROTOCOL:
            raise RuntimeError(
                "This computer uses an incompatible Admin authority protocol. "
                "Update GameGrid on every cafe PC.")
        if (not current or incoming.get("pool_id") != current["pool_id"]
                or sender not in current["members"]
                or incoming.get("active_admin", {}).get("pc_id") != sender):
            raise PermissionError("Invalid Admin snapshot owner or pool.")
        if admin_rank(incoming) != admin_rank(current):
            proof = {"pool_id": current["pool_id"], "candidate": sender,
                     "term": incoming["active_admin"]["term"]}
            verify_admin_proof(current["admin_signing"]["public"], proof,
                               incoming["active_admin"].get("proof") or "")
        applied = self.store.apply_snapshot(
            incoming, follow_live_admin=follow_live_admin and not self.is_admin()
        )
        if applied and not self.is_admin():
            if incoming["active_admin"].get("pc_id") != self.store.pc_id:
                self.handoff_deadline = 0
            self.admin_ui_open = False
            self.notify("role_lost")
        return applied

    def update_admin_connection_note(self) -> None:
        snap = self.snapshot()
        if not snap:
            return
        admin_id = snap["active_admin"].get("pc_id")
        remote = self.peer_status(admin_id) if admin_id else None
        connected = self.is_admin() or bool(
            remote and remote.get("admin")
            and int(remote.get("authority_protocol", 0)) == AUTHORITY_PROTOCOL
            and self._admin_claim_fresh(
                str(admin_id), int(snap["active_admin"].get("term", 0))))
        note = ("Admin connected" if connected else
                "Admin unavailable — current session continues locally; changes will sync."
                if snap["sessions"].get(self.store.pc_id) else
                "Admin unavailable — reconnecting. Eligible idle PCs may use Switch to Admin.")
        if self.store.local("admin_connection_note") != note:
            self.store.set_local("admin_connection_note", note)

    def _require_admin(self, allow_handoff: bool = False) -> None:
        if not self.is_admin():
            raise PermissionError("An active Admin lease is required.")
        transfer = self.handoff_transfer
        if transfer and transfer.get("expires_at", 0) <= time.time():
            self.handoff_transfer = None
            transfer = None
        if transfer and not allow_handoff:
            raise RuntimeError(
                "Admin transfer is in progress; authoritative changes are paused.")

    def _require_customer_admin(self) -> None:
        """Account management additionally requires an authenticated Admin UI."""
        self._require_admin()
        if not self.admin_ui_open or self.admin_signing_key is None:
            raise PermissionError("Authenticated Admin staff access is required.")

    def _pairing_removal_active(self) -> bool:
        if self.store.local("pending_pairing_revocation"):
            return True
        removal = self.pairing_removal
        if removal and removal.get("expires_at", 0) <= time.time():
            self.pairing_removal = None
            removal = None
        return removal is not None

    def _require_pairing_available(self) -> None:
        if self._pairing_removal_active():
            raise RuntimeError(
                "This computer is being removed from the cafe; new access "
                "cannot start.")

    def receive_pairing_revocation(self, notice: dict) -> bool:
        """Apply only an Admin-signed revocation for this exact authorization."""
        snap = self.snapshot()
        if not snap:
            return False
        member = snap.get("members", {}).get(self.store.pc_id)
        if not member:
            return False
        certificate = verify_pairing_revocation(
            snap, notice, self.store.pc_id, member.get("public_key"),
            member.get("authorization_id"))
        saved_notice = json.dumps(notice, separators=(",", ":"))
        first_notice = self.store.local(
            "pending_pairing_revocation") != saved_notice
        self.store.set_local("pending_pairing_revocation", saved_notice)
        self.pairing_removal = {
            "revocation_id": certificate["revocation_id"],
            "expires_at": float("inf"),
        }
        if (snap.get("sessions", {}).get(self.store.pc_id)
                or self.store.local_customer_session()
                or self.retainer_unlock_pending):
            if first_notice:
                self.notify("pairing_revocation_pending", {
                    "pc_name": certificate["pc_name"]})
            return False
        pool_id = snap["pool_id"]
        cafe_name = snap.get("cafe_name", "the cafe")
        if not self.store.clear_pairing_authorization(pool_id):
            return False
        self.admin_signing_key = None
        self.admin_ui_open = False
        self.pairing_removal = None
        self.handoff_transfer = None
        self.handoff_deadline = 0
        self.last_access_allowed = None
        with self.lock:
            self.peer_statuses.clear()
            self.connection_states.clear()
            self.graceful_disconnects.clear()
        self.notify("pairing_revoked", {"cafe_name": cafe_name})
        return True

    def _apply_pending_pairing_revocation(self) -> bool:
        raw = self.store.local("pending_pairing_revocation")
        if not raw:
            return False
        try:
            notice = json.loads(raw)
        except (TypeError, ValueError):
            self.store.delete_local("pending_pairing_revocation")
            return False
        return self.receive_pairing_revocation(notice)

    def customer_admin_state(self, search: str = "",
                             selected_id: str = "") -> dict:
        self._require_customer_admin()
        snap = self.snapshot()
        accounts = self.store.customer_accounts()
        members = snap["members"]
        ownerships = {item["customer_id"]: item
                      for item in self.store.customer_ownerships()}
        for account in accounts:
            cooldowns = self.store.customer_cooldown_state(account)
            account.update({
                "retainerCooldownMinutes": cooldowns["retainer_minutes"],
                "retainerCooldownExpiresAt": cooldowns[
                    "retainer_expires_at"],
                "retainerCooldownRemaining": cooldowns[
                    "retainer_remaining"],
                "loginCooldownMinutes": cooldowns["login_minutes"],
                "loginCooldownExpiresAt": cooldowns["login_expires_at"],
                "loginCooldownRemaining": cooldowns["login_remaining"],
            })
            records = self.store.customer_sync_records(account["customer_id"])
            sync_peers = []
            for pc_id, member in members.items():
                if pc_id == self.store.pc_id:
                    continue
                saved = records.get(pc_id, {})
                acknowledged = int(saved.get("revision", 0))
                online = self.peer_status(pc_id) is not None
                if acknowledged >= account["revision"]:
                    status = "Synced"
                elif not online:
                    status = "Pending / Offline"
                elif saved.get("error"):
                    status = "Error"
                else:
                    status = "Synchronizing"
                sync_peers.append({
                    "pcId": pc_id, "name": member["name"],
                    "revision": acknowledged, "status": status,
                })
            statuses = {item["status"] for item in sync_peers}
            account["syncPeers"] = sync_peers
            account["syncStatus"] = (
                "Error" if "Error" in statuses else
                "Pending / Offline" if "Pending / Offline" in statuses else
                "Synchronizing" if "Synchronizing" in statuses else "Synced"
            )
            ownership = ownerships.get(account["customer_id"])
            retainer = (self.store.retainer_for_login(
                account["customer_id"], ownership["pc_id"],
                ownership["login_id"]) if ownership else None)
            account["retainerInProgress"] = bool(
                retainer and retainer["state"] in ("prepared", "active"))
            account["retainerActive"] = bool(
                retainer and retainer["state"] == "active")
            account["loggedInPcId"] = ownership["pc_id"] if ownership else ""
            account["status"] = (
                "Logged in on " + members.get(ownership["pc_id"], {}).get(
                    "name", ownership["pc_id"])
                if ownership else "Not logged in"
            )
        selected = next((item for item in accounts
                         if item["customer_id"] == selected_id), None)
        normalized_search = " ".join(search.split()).casefold()
        visible_accounts = [
            item for item in accounts
            if not normalized_search or normalized_search in item["name"].casefold()
        ]
        return {
            "accounts": visible_accounts,
            "selected": selected,
            "adjustments": (self.store.customer_adjustments(selected_id)
                            if selected else []),
        }

    def create_customer(self, name: str, operation_id: str) -> dict:
        with self.lock:
            self._require_customer_admin()
            account = self.store.create_customer(
                name, self.snapshot()["admin_name"], operation_id)
        self.notify("changed")
        return account

    def preview_customer_adjustment(self, customer_id: str, action: str,
                                    hours: str, minutes: str) -> dict:
        self._require_customer_admin()
        if self.store.customer_retainer_in_progress(customer_id):
            raise RuntimeError(
                "Balance adjustments are unavailable during an active retainer "
                "session. End the session before making changes."
            )
        if action not in ("add", "reduce"):
            raise ValueError("Choose Add or Reduce.")
        seconds = retainer_seconds(hours, minutes)
        account = self.store.customer_account(customer_id)
        if not account:
            raise ValueError("Choose an existing customer.")
        resulting = (account["balance_seconds"] + seconds if action == "add"
                     else account["balance_seconds"] - seconds)
        if resulting < 0:
            raise ValueError("Retainer balance cannot be reduced below zero.")
        return {
            "customerId": customer_id, "name": account["name"],
            "action": action, "seconds": seconds,
            "previousBalance": account["balance_seconds"],
            "resultingBalance": resulting,
            "revision": account["revision"], "operationId": uuid.uuid4().hex,
        }

    def adjust_customer(self, preview: dict) -> dict:
        with self.lock:
            self._require_customer_admin()
            account = self.store.adjust_customer_balance(
                preview["customerId"], preview["action"],
                int(preview["seconds"]), int(preview["revision"]),
                int(preview["previousBalance"]), self.snapshot()["admin_name"],
                preview["operationId"])
        self.notify("changed")
        return account

    def delete_customer(self, customer_id: str, exact_name: str,
                        expected_revision: int, operation_id: str) -> int:
        with self.lock:
            self._require_customer_admin()
            revision = self.store.delete_customer(
                customer_id, exact_name, expected_revision, operation_id)
        self.notify("changed")
        return revision

    def release_customer_ownership(self, customer_id: str) -> None:
        """Authenticated Admin recovery for an abandoned registered login."""
        with self.lock:
            self._require_customer_admin()
            ownership = self.store.customer_ownership(customer_id=customer_id)
            if not ownership:
                return
            active = self.store.retainer_for_login(
                customer_id, ownership["pc_id"], ownership["login_id"])
            if active and active["state"] == "active":
                self._checkpoint_active_retainers(
                    force=True, customer_id=customer_id)
            self._force_retainer_target(active or {
                "customer_id": customer_id,
                "pc_id": ownership["pc_id"],
                "login_id": ownership["login_id"],
                "balance_seconds": self.store.customer_account(
                    customer_id)["balance_seconds"],
            }, logout=True, reason="admin_release")
            self.store.force_release_customer(customer_id)
        self.notify("changed")

    def set_customer_cooldown_overrides(
            self, customer_id: str, retainer_minutes: int | None,
            login_minutes: int | None, expected_revision: int) -> dict:
        with self.lock:
            self._require_customer_admin()
            account = self.store.set_customer_cooldown_overrides(
                customer_id, retainer_minutes, login_minutes,
                expected_revision, self.snapshot()["admin_name"])
        self.notify("changed")
        return account

    def clear_customer_cooldown(self, customer_id: str, kind: str) -> dict:
        with self.lock:
            self._require_customer_admin()
            account = self.store.clear_customer_cooldown(
                customer_id, kind, self.snapshot()["admin_name"])
        self.notify("changed")
        return account

    def active_retainer_shutdown_message(self) -> str:
        active = self.store.active_retainers() if self.is_admin() else []
        if not active:
            return (
                "Exiting disconnects this Admin PC. Existing Guest sessions on "
                "other User PCs will continue independently."
            )
        snap = self.snapshot()
        names = [snap["members"].get(item["pc_id"], {}).get(
            "name", item["pc_id"]) for item in active]
        return (
            "Exiting disconnects this Admin PC. Active retainer play will be "
            "stopped and locked on: " + ", ".join(names) + ". Existing Guest "
            "sessions on other PCs continue independently."
        )

    @staticmethod
    def _local_customer_from_state(state: dict,
                                   feedback: str = "") -> dict:
        account = state["account"]
        ownership = state["ownership"]
        retainer = state.get("retainer") or {}
        cooldowns = state.get("cooldowns") or {}
        return {
            "customer_id": account["customer_id"], "name": account["name"],
            "login_id": ownership["login_id"],
            "balance_seconds": account["balance_seconds"],
            "revision": account["revision"],
            "retainer_session_id": retainer.get("session_id", ""),
            "retainer_active": retainer.get("state") == "active",
            "retainer_state": retainer.get("state", ""),
            "retainer_display_seconds": int(state.get(
                "retainer_display_seconds", account["balance_seconds"])),
            "retainer_display_at": float(state.get(
                "retainer_display_at", time.time())),
            "retainer_display_received_monotonic": time.monotonic(),
            "retainer_finalizing": False,
            "displayed_at": time.time(), "last_admin_seen": time.time(),
            "retainer_cooldown_expires_at": int(
                cooldowns.get("retainer_expires_at", 0)),
            "login_cooldown_expires_at": int(
                cooldowns.get("login_expires_at", 0)),
            "feedback": feedback,
        }

    def _customer_state_snapshot(self, customer_id: str, pc_id: str,
                                 login_id: str, now: float) -> dict:
        """Attach current display metadata without changing the saved balance."""
        state = self.store.customer_login_state(
            customer_id, pc_id, login_id, touch_at=now)
        retainer = state.get("retainer") or {}
        remaining = int(state["account"]["balance_seconds"])
        if retainer.get("state") == "active":
            clock = self.retainer_clocks.get(retainer["session_id"])
            if clock:
                remaining = max(
                    0, remaining - int(max(0, time.monotonic() - clock[0])))
        state["retainer_display_seconds"] = remaining
        state["retainer_display_at"] = now
        return state

    def _customer_admin_request(self, action: str, data: dict) -> dict:
        payload = {"action": action, **data}
        if self.is_admin():
            return self._handle_customer_access(payload, self.store.pc_id)
        ip = self.active_admin_ip()
        if ip and not self.confirmed_remote_admin_online():
            try:
                status = self.network.call(ip, "status", {})
                snap = self.snapshot()
                claim = snap["active_admin"] if snap else {}
                if (status.get("pc_id") == claim.get("pc_id")
                        and status.get("admin")
                        and int(status.get("admin_term", -1))
                        == int(claim.get("term", 0))
                        and int(status.get("authority_protocol", 0))
                        == AUTHORITY_PROTOCOL):
                    self.peer_statuses[status["pc_id"]] = (time.time(), status)
            except Exception:
                pass
        if not ip or not self.confirmed_remote_admin_online():
            raise RuntimeError("Admin is offline. Registered access is unavailable.")
        return self.network.call(ip, "customer_access", payload)

    def _record_customer_auth_failure(self, sender: str,
                                      customer_id: str) -> None:
        key, now = (sender, customer_id), time.monotonic()
        count, blocked_until = self.customer_auth_failures.get(key, (0, 0.0))
        if blocked_until > now:
            raise PermissionError(
                f"Too many attempts. Try again in {max(1, int(blocked_until - now))} seconds."
            )
        count += 1
        self.customer_auth_failures[key] = (
            0, now + CUSTOMER_AUTH_BLOCK_SECONDS
        ) if count >= CUSTOMER_AUTH_ATTEMPTS else (count, 0.0)

    def _check_customer_auth_limit(self, sender: str,
                                   customer_id: str) -> None:
        _count, blocked_until = self.customer_auth_failures.get(
            (sender, customer_id), (0, 0.0))
        now = time.monotonic()
        if blocked_until > now:
            raise PermissionError(
                f"Too many attempts. Try again in {max(1, int(blocked_until - now))} seconds."
            )

    def _handle_customer_access(self, data: dict, sender: str) -> dict:
        """Apply registered-customer requests only on the active Admin."""
        self._require_admin()
        snap = self.snapshot()
        if sender not in snap["members"]:
            raise PermissionError("This PC is not registered in the cafe.")
        action = str(data.get("action", ""))
        customer_id = str(data.get("customer_id", ""))
        now = time.time()
        if action == "login":
            self._check_customer_auth_limit(sender, customer_id)
            if snap["sessions"].get(sender):
                raise RuntimeError(
                    "End the current ordinary session before customer login."
                )
            try:
                state = self.store.authenticate_customer(
                    customer_id, str(data.get("password", "")), sender,
                    str(data.get("operation_id", "")), now)
            except PermissionError:
                self._record_customer_auth_failure(sender, customer_id)
                raise PermissionError("Customer name or password is incorrect.")
            self.customer_auth_failures.pop((sender, customer_id), None)
            self.notify("changed")
            return state

        login_id = str(data.get("login_id", ""))
        if action == "state":
            return self._customer_state_snapshot(
                customer_id, sender, login_id, now)
        if action == "password":
            new_password = str(data.get("new_password", ""))
            if new_password != str(data.get("confirmation", "")):
                raise ValueError("New password and confirmation do not match.")
            account = self.store.change_customer_password(
                customer_id, sender, login_id,
                str(data.get("current_password", "")), new_password, now)
            self.notify("changed")
            return self._customer_state_snapshot(
                customer_id, sender, login_id, now)
        if action == "logout":
            if snap["sessions"].get(sender):
                raise RuntimeError(
                    "End the ordinary gaming session before logging out."
                )
            self.store.release_customer_login(
                customer_id, sender, login_id, record_logout=True, now=now)
            self.notify("changed")
            return {"logged_out": True}
        if action == "retainer_prepare":
            if snap["sessions"].get(sender):
                raise RuntimeError(
                    "End the ordinary gaming session before using retainer time."
                )
            retainer = self.store.prepare_retainer(
                customer_id, sender, login_id,
                str(data.get("operation_id", "")), now)
            return {"retainer": retainer}
        if action == "retainer_activate":
            retainer = self.store.activate_retainer(
                str(data.get("session_id", "")), customer_id, sender,
                login_id, now)
            # A duplicate activation must not reset the authoritative clock.
            self.retainer_clocks.setdefault(
                retainer["session_id"], (time.monotonic(), now))
            self.notify("changed")
            return self._customer_state_snapshot(
                customer_id, sender, login_id, now)
        if action == "retainer_abort":
            session_id = str(data.get("session_id", ""))
            self.store.abort_retainer(session_id, sender, login_id)
            self.retainer_clocks.pop(session_id, None)
            return {"aborted": True}
        if action == "retainer_stop":
            session_id = str(data.get("session_id", ""))
            state = self._stop_authoritative_retainer(
                session_id, customer_id, sender, login_id, "customer")
            self.notify("changed")
            return state
        raise ValueError("Unsupported customer account request.")

    def login_customer(self, customer_id: str, password: str,
                       operation_id: str) -> dict:
        self._require_pairing_available()
        if self.store.local_customer_session():
            raise RuntimeError("A registered customer is already logged in.")
        snap = self.snapshot()
        if snap["sessions"].get(self.store.pc_id):
            raise RuntimeError("End the ordinary session before customer login.")
        state = self._customer_admin_request("login", {
            "customer_id": customer_id, "password": password,
            "operation_id": operation_id,
        })
        local = self._local_customer_from_state(state, "Logged in")
        self.store.save_local_customer_session(local)
        self.notify("changed")
        return local

    def change_logged_in_customer_password(
            self, current_password: str, new_password: str,
            confirmation: str) -> dict:
        local = self.store.local_customer_session()
        if not local:
            raise PermissionError("Select and log in to a customer account first.")
        state = self._customer_admin_request("password", {
            "customer_id": local["customer_id"],
            "login_id": local["login_id"],
            "current_password": current_password,
            "new_password": new_password, "confirmation": confirmation,
        })
        updated = self._local_customer_from_state(
            state, "Password changed successfully")
        self.store.save_local_customer_session(updated)
        return updated

    def logout_customer(self) -> None:
        local = self.store.local_customer_session()
        if not local:
            return
        if local.get("retainer_active"):
            self.stop_local_retainer()
            local = self.store.local_customer_session()
        if self.snapshot()["sessions"].get(self.store.pc_id):
            raise RuntimeError("End the ordinary session before logging out.")
        self._customer_admin_request("logout", {
            "customer_id": local["customer_id"],
            "login_id": local["login_id"],
        })
        self.store.clear_local_customer_session()
        self.store.set_local("customer_feedback", "Returned to Guest mode.")
        self._reconcile_local_access()

    def start_local_retainer(self, operation_id: str) -> dict:
        self._require_pairing_available()
        local = self.store.local_customer_session()
        if not local:
            raise PermissionError("Log in to a registered customer account first.")
        if local.get("retainer_active"):
            return local
        if self.snapshot()["sessions"].get(self.store.pc_id):
            raise RuntimeError(
                "End the ordinary gaming session before using retainer time."
            )
        prepared = self._customer_admin_request("retainer_prepare", {
            "customer_id": local["customer_id"],
            "login_id": local["login_id"], "operation_id": operation_id,
        })["retainer"]
        self.retainer_unlock_pending = True
        try:
            if not self.default_handle:
                raise RuntimeError("The gaming desktop is unavailable.")
            switch(self.default_handle)
            if active_desktop_name() != "Default":
                raise RuntimeError("The gaming desktop did not unlock.")
            state = self._customer_admin_request("retainer_activate", {
                "customer_id": local["customer_id"],
                "login_id": local["login_id"],
                "session_id": prepared["session_id"],
            })
            updated = self._local_customer_from_state(
                state, "Retainer play is active")
            self.store.save_local_customer_session(updated)
            self.last_access_allowed = True
            return updated
        except Exception:
            try:
                self._customer_admin_request("retainer_abort", {
                    "customer_id": local["customer_id"],
                    "login_id": local["login_id"],
                    "session_id": prepared["session_id"],
                })
            except Exception:
                pass
            raise
        finally:
            self.retainer_unlock_pending = False
            if not (self.store.local_customer_session() or {}).get(
                    "retainer_active"):
                self._reconcile_local_access()

    def stop_local_retainer(self) -> dict:
        local = self.store.local_customer_session()
        if not local or not local.get("retainer_active"):
            return local or {}
        if self.console_handle:
            local["retainer_finalizing"] = True
            self.store.save_local_customer_session(local)
            switch(self.console_handle)
        self.last_access_allowed = False
        try:
            state = self._customer_admin_request("retainer_stop", {
                "customer_id": local["customer_id"],
                "login_id": local["login_id"],
                "session_id": local["retainer_session_id"],
            })
            updated = self._local_customer_from_state(
                state, "Retainer stopped; remaining time was saved")
            self.store.save_local_customer_session(updated)
            self.store.post_locked_notice(
                "Retainer Stopped",
                "Your remaining time has been saved.")
            return updated
        except Exception:
            self._force_local_customer_logout(
                "Admin unavailable. Retainer play stopped and the account was logged out.")
            raise

    def _stop_authoritative_retainer(self, session_id: str, customer_id: str,
                                     pc_id: str, login_id: str,
                                     reason: str) -> dict:
        state = self.store.customer_login_state(customer_id, pc_id, login_id)
        retainer = state.get("retainer")
        if not retainer or retainer["session_id"] != session_id:
            raise RuntimeError("Retainer session has changed.")
        if retainer["state"] == "active":
            clock = self.retainer_clocks.get(session_id)
            elapsed = int(max(0, time.monotonic() - clock[0])) if clock else 0
            self.store.consume_retainer(
                session_id, elapsed, finalize=True, reason=reason)
        elif retainer["state"] == "prepared":
            self.store.abort_retainer(session_id, pc_id, login_id, reason)
        self.retainer_clocks.pop(session_id, None)
        return self._customer_state_snapshot(
            customer_id, pc_id, login_id, time.time())

    def _checkpoint_active_retainers(self, force: bool = False,
                                     customer_id: str = "") -> None:
        if not self.is_admin():
            return
        now_mono, now_wall = time.monotonic(), time.time()
        exhausted: list[dict] = []
        for retainer in self.store.active_retainers():
            if customer_id and retainer["customer_id"] != customer_id:
                continue
            clock = self.retainer_clocks.setdefault(
                retainer["session_id"], (now_mono, now_wall))
            elapsed = max(0.0, now_mono - clock[0])
            if not force and elapsed + SYNC_INTERVAL_SECONDS < RETAINER_CHECKPOINT_SECONDS:
                continue
            seconds = int(elapsed)
            if seconds <= 0:
                continue
            result = self.store.consume_retainer(
                retainer["session_id"], seconds, now=now_wall)
            self.retainer_clocks[retainer["session_id"]] = (
                clock[0] + seconds, now_wall)
            self.notify("changed")
            if result["exhausted"]:
                self.retainer_clocks.pop(retainer["session_id"], None)
                exhausted.append(result["retainer"])
        for retainer in exhausted:
            self._force_retainer_target(retainer, logout=False,
                                        reason="exhausted")

    def _force_retainer_target(self, retainer: dict, logout: bool,
                               reason: str) -> None:
        data = {
            "customer_id": retainer["customer_id"],
            "login_id": retainer["login_id"],
            "balance_seconds": int(retainer.get("balance_seconds", 0)),
            "logout": logout, "reason": reason,
        }
        pc_id = retainer["pc_id"]
        try:
            if pc_id == self.store.pc_id:
                self._apply_retainer_control(data)
                return
            peer = next((item for item in self.store.peers()
                         if item["pc_id"] == pc_id), None)
            if peer and self.peer_status(pc_id):
                self.network.call(peer["ip"], "retainer_control", data)
        except Exception:
            pass  # Heartbeat loss applies the same lock/logout policy shortly.

    def _apply_retainer_control(self, data: dict) -> None:
        local = self.store.local_customer_session()
        if not local or (local.get("customer_id") != data.get("customer_id")
                         or local.get("login_id") != data.get("login_id")):
            return
        if data.get("logout"):
            self._force_local_customer_logout(
                "Admin ended this registered login. Returned to Guest mode.")
            return
        local.update({
            "balance_seconds": max(0, int(data.get("balance_seconds", 0))),
            "retainer_active": False, "retainer_state": "stopped",
            "displayed_at": time.time(), "last_admin_seen": time.time(),
            "feedback": ("Retainer time exhausted. Ask staff to add time."
                         if data.get("reason") in ("exhausted", "balance_reduced")
                         else "Retainer play stopped."),
        })
        self.store.save_local_customer_session(local)
        self.store.set_local("customer_feedback", local["feedback"])
        self._reconcile_local_access()
        if data.get("reason") in ("exhausted", "balance_reduced"):
            self.store.post_locked_notice(
                "Retainer Time Exhausted",
                "Your retainer balance has reached zero. Please contact "
                "staff to add more time.")

    def _force_local_customer_logout(self, message: str) -> None:
        if self.snapshot()["sessions"].get(self.store.pc_id):
            self.finish_local_session("interrupted", reconcile_access=False)
        self.store.clear_local_customer_session()
        self.store.set_local("customer_feedback", message)
        self._reconcile_local_access()

    def _hold_local_claim(self, term: int) -> None:
        """A local timeout stops a disconnected former Admin from writing."""
        if time.monotonic() < self.handoff_deadline:
            raise RuntimeError("This PC just handed off Admin control; wait and retry.")
        self.store.set_local(self._lease_key("pc"), self.store.pc_id)
        self.store.set_local(self._lease_key("run"), self.run_id)
        self.store.set_local(self._lease_key("term"), str(term))
        self.store.set_local(self._lease_key("until"),
                             str(time.time() + LEASE_SECONDS))
        self.local_claim_deadline = time.monotonic() + LEASE_SECONDS

    def can_become_admin(self) -> bool:
        snap = self.snapshot()
        return bool(snap and not snap["sessions"].get(self.store.pc_id)
                    and not self.store.local_customer_session())

    def take_admin(self, password: str, progress=None) -> None:
        """Authenticate, then log in locally or use the existing live handoff."""
        progress = progress or (lambda _stage: None)
        progress("Authenticating Admin authority")
        self._require_pairing_available()
        live_admin = self._find_live_admin()
        snap = self.snapshot()
        if not snap or not verify_password(password, snap["admin_password"]):
            raise PermissionError("Incorrect Admin password.")
        self.admin_signing_key = unlock_admin_signing_key(
            password, snap["admin_signing"]
        )
        if self._resume_pending_admin_handoff():
            progress("Admin authority transfer completed")
            return
        if not self.can_become_admin():
            raise RuntimeError("End this PC's customer session before switching to Admin.")
        if live_admin:
            self._take_from_active_admin(live_admin[1], progress)
        else:
            progress("Finalizing local Admin authority")
            self.login_admin_with_unlocked_key()
            progress("Admin authority ready")

    def _take_from_active_admin(self, ip: str, progress=None) -> None:
        progress = progress or (lambda _stage: None)
        progress("Preparing Admin transfer")
        diagnostic_info("HANDOFF", "TRANSFER_INITIATED",
                        candidate=fingerprint(self.store.pc_id), ip=ip)
        latest = self.network.call(ip, "snapshot", {})["snapshot"]
        self.accept_admin_snapshot(latest, latest["active_admin"]["pc_id"])
        snap = self.snapshot()
        previous = snap["active_admin"].get("pc_id")
        if previous == self.store.pc_id:
            return self.login_admin_with_unlocked_key()
        term = max(int(snap["active_admin"].get("term", 0)),
                   int(self.store.local(self._lease_key("term")) or 0)) + 1
        challenge = self.network.call(ip, "lease_challenge", {})["challenge"]
        transfer_id = uuid.uuid4().hex
        recipient_private, recipient_public = create_handoff_keypair()
        context = {
            "pool_id": snap["pool_id"], "source_admin": previous,
            "candidate": self.store.pc_id, "term": term,
            "transfer_id": transfer_id, "challenge": challenge,
        }
        proof = {**context, "recipient_public": recipient_public}
        prepared = False
        committed = False
        try:
            reply = self.network.call(ip, "prepare_admin_transfer", {
                "proof": proof,
                "signature": sign_admin_proof(self.admin_signing_key, proof),
            })
            prepared = True
            progress("Transferring authoritative data")
            manifest = reply.get("manifest", {})
            ciphertext_bytes = int(manifest.get("ciphertext_bytes", 0))
            expected_chunks = (
                ciphertext_bytes * 2 + HANDOFF_CHUNK_HEX_LENGTH - 1
            ) // HANDOFF_CHUNK_HEX_LENGTH
            if (not isinstance(manifest, dict)
                    or manifest.get("context") != context
                    or ciphertext_bytes < 16
                    or ciphertext_bytes > MAX_HANDOFF_PLAINTEXT + 16
                    or int(manifest.get("chunk_count", 0)) != expected_chunks):
                raise ValueError("Admin handoff manifest is invalid.")
            chunks = []
            for index in range(int(manifest["chunk_count"])):
                part = self.network.call(ip, "admin_transfer_chunk", {
                    "transfer_id": transfer_id, "index": index})
                chunk = part.get("chunk")
                if (not isinstance(chunk, str)
                        or len(chunk) > HANDOFF_CHUNK_HEX_LENGTH):
                    raise ValueError("Admin handoff chunk is invalid.")
                chunks.append(chunk)
            ciphertext = "".join(chunks)
            if (len(ciphertext) != int(manifest["ciphertext_bytes"]) * 2
                    or hashlib.sha256(bytes.fromhex(ciphertext)).hexdigest()
                    != manifest.get("ciphertext_digest")):
                raise ValueError("Encrypted Admin handoff is incomplete or corrupt.")
            bundle = open_admin_handoff(recipient_private, context, {
                "sender_public": manifest.get("sender_public"),
                "nonce": manifest.get("nonce"), "ciphertext": ciphertext,
            })
            progress("Verifying and saving transferred data")
            saved_digest = self.store.apply_authoritative_handoff_bundle(
                bundle, previous, self.store.pc_id, term, transfer_id)
            if saved_digest != manifest.get("state_digest"):
                raise RuntimeError("Admin handoff durable verification failed.")
            self.store.set_local(self._pending_handoff_key(), json.dumps({
                "source_admin": previous, "source_ip": ip, "term": term,
                "transfer_id": transfer_id,
            }, separators=(",", ":")))
            claim = {"pool_id": snap["pool_id"],
                     "candidate": self.store.pc_id, "term": term}
            try:
                progress("Finalizing Admin authority")
                commit = self.network.call(ip, "commit_admin_transfer", {
                    "transfer_id": transfer_id, "state_digest": saved_digest,
                    "claim": claim,
                    "claim_signature": sign_admin_proof(
                        self.admin_signing_key, claim),
                })
                committed_snapshot = commit["snapshot"]
            except Exception as error:
                committed_snapshot = self._resolve_handoff_commit(
                    ip, previous, term, transfer_id, error)
            self._complete_committed_handoff(
                committed_snapshot, previous, term, transfer_id)
            committed = True
            progress("Admin authority transfer completed")
            diagnostic_info("HANDOFF", "TRANSFER_COMPLETED",
                            source=fingerprint(previous),
                            candidate=fingerprint(self.store.pc_id), term=term,
                            transfer=fingerprint(transfer_id))
        finally:
            if prepared and not committed:
                diagnostic_warning("HANDOFF", "TRANSFER_INCOMPLETE",
                                   candidate=fingerprint(self.store.pc_id),
                                   transfer=fingerprint(transfer_id))
                pending = self.store.local(self._pending_handoff_key())
                if not pending:
                    try:
                        self.network.call(ip, "abort_admin_transfer", {
                            "transfer_id": transfer_id})
                    except Exception:
                        pass

    def _pending_handoff_key(self) -> str:
        return f"pending_admin_handoff:{self.store.current_pool_id or 'none'}"

    def _complete_committed_handoff(self, snapshot: dict, source_admin: str,
                                    term: int, transfer_id: str) -> None:
        current = self.snapshot()
        claim = snapshot.get("active_admin", {})
        proof = {"pool_id": current["pool_id"],
                 "candidate": self.store.pc_id, "term": int(term)}
        if (snapshot.get("pool_id") != current["pool_id"]
                or source_admin not in current["members"]
                or claim.get("pc_id") != self.store.pc_id
                or int(claim.get("term", -1)) != int(term)
                or claim.get("handoff_id") != transfer_id):
            raise PermissionError("Committed Admin handoff is invalid.")
        verify_admin_proof(current["admin_signing"]["public"], proof,
                           claim.get("proof", ""))
        self.store.apply_snapshot(snapshot, follow_live_admin=True)
        def refresh_committed_claim(state):
            active = state["active_admin"]
            if (active.get("pc_id") != self.store.pc_id
                    or int(active.get("term", -1)) != int(term)
                    or active.get("handoff_id") != transfer_id):
                raise RuntimeError("Committed Admin authority changed before activation.")
            active["expires_at"] = time.time() + LEASE_SECONDS

        self.store.update(refresh_committed_claim)
        self._hold_local_claim(term)
        self.store.delete_local(self._pending_handoff_key())
        self.admin_ui_open = True
        self.notify("admin_ready")
        self._replicate_once()

    def _resolve_handoff_commit(self, ip: str, source_admin: str, term: int,
                                transfer_id: str, original_error: Exception) -> dict:
        try:
            snapshot = self.network.call(ip, "snapshot", {})["snapshot"]
        except Exception as query_error:
            raise RuntimeError(
                "Admin transfer outcome could not be confirmed. Authoritative "
                "changes remain disabled until the previous Admin reconnects."
            ) from query_error
        claim = snapshot.get("active_admin", {})
        if (claim.get("pc_id") == self.store.pc_id
                and int(claim.get("term", -1)) == int(term)
                and claim.get("handoff_id") == transfer_id):
            return snapshot
        self.store.delete_local(self._pending_handoff_key())
        try:
            self.network.call(ip, "abort_admin_transfer", {
                "transfer_id": transfer_id})
        except Exception:
            pass
        raise original_error

    def _resume_pending_admin_handoff(self) -> bool:
        raw = self.store.local(self._pending_handoff_key())
        if not raw:
            return False
        try:
            pending = json.loads(raw)
            source = str(pending["source_admin"])
            term = int(pending["term"])
            transfer_id = str(pending["transfer_id"])
            ip = str(pending.get("source_ip", ""))
            if not ip:
                ip = next(item["ip"] for item in self.store.peers()
                          if item["pc_id"] == source)
            snapshot = self.network.call(ip, "snapshot", {})["snapshot"]
        except Exception as error:
            raise RuntimeError(
                "A previous Admin transfer is unresolved. Reconnect the previous "
                "Admin before retrying."
            ) from error
        claim = snapshot.get("active_admin", {})
        if (claim.get("pc_id") == self.store.pc_id
                and int(claim.get("term", -1)) == term
                and claim.get("handoff_id") == transfer_id):
            self._complete_committed_handoff(
                snapshot, source, term, transfer_id)
            return True
        self.store.delete_local(self._pending_handoff_key())
        return False

    def login_admin_with_unlocked_key(self) -> None:
        """Only the saved Admin Node can renew its own designation locally."""
        if not self.can_become_admin():
            raise RuntimeError("A PC with an active customer session cannot be Admin.")
        if self.admin_signing_key is None:
            raise PermissionError("Admin password authorization is required.")
        if self.is_admin():
            self.admin_ui_open = True
            self.notify("admin_ready")
            return
        if self._find_live_admin():
            raise RuntimeError("An active Admin is online; use Switch to Admin to transfer.")
        snap = self.snapshot()
        if snap["active_admin"].get("pc_id") != self.store.pc_id:
            raise RuntimeError("The designated Admin Node is offline; Admin transfer requires it to be online.")
        if int(self.store.local(self._lease_key("handed_off")) or 0) >= \
                int(snap["active_admin"].get("term", 0)):
            raise RuntimeError("This PC handed off Admin control; reconnect to the current Admin Node.")
        term = max(int(snap["active_admin"].get("term", 0)),
                   int(self.store.local(self._lease_key("term")) or 0)) + 1
        self._hold_local_claim(term)
        self._activate_admin(term)

    def _activate_admin(self, term: int) -> None:
        if self.admin_signing_key is None:
            raise PermissionError("Admin password authorization is required.")
        if (self.store.local(self._lease_key("pc")) != self.store.pc_id
                or int(self.store.local(self._lease_key("term")) or 0) != term
                or self.local_claim_deadline <= time.monotonic()):
            raise RuntimeError("Admin claim expired before activation; retry.")

        def claim(state):
            active = state["active_admin"]
            if (active.get("term", 0) >= term
                    and active.get("pc_id") != self.store.pc_id):
                raise RuntimeError("A newer Admin election already won.")
            proof = {"pool_id": state["pool_id"],
                     "candidate": self.store.pc_id, "term": term}
            state["active_admin"] = {
                "pc_id": self.store.pc_id, "term": term,
                "expires_at": time.time() + LEASE_SECONDS,
                "proof": sign_admin_proof(self.admin_signing_key, proof),
            }
            member = state["members"][self.store.pc_id]
            member["last_connected_at"] = max(
                float(member.get("last_connected_at", 0)), self.started_at)
        self.store.update(claim)
        diagnostic_info("AUTHORITY", "CLAIM_CREATED",
                        admin=fingerprint(self.store.pc_id), term=term,
                        protocol=AUTHORITY_PROTOCOL)
        self.admin_ui_open = True
        self.notify("admin_ready")
        self._replicate_once()

    def login_admin(self, password: str) -> None:
        """Password check followed by a first-available LAN Admin claim."""
        self._require_pairing_available()
        snap = self.snapshot()
        if not snap or not verify_password(password, snap["admin_password"]):
            raise PermissionError("Incorrect Admin password.")
        self.admin_signing_key = unlock_admin_signing_key(
            password, snap["admin_signing"]
        )
        self.login_admin_with_unlocked_key()

    def _renew_admin_lease(self) -> None:
        if not self.is_admin():
            return
        with self.lock:
            if not self.is_admin():
                return
            term = self.snapshot()["active_admin"]["term"]
            self._hold_local_claim(term)

            def extend(state):
                active = state["active_admin"]
                if active.get("pc_id") != self.store.pc_id or active.get("term") != term:
                    raise RuntimeError("Admin ownership changed during renewal.")
                active["expires_at"] = time.time() + LEASE_SECONDS

            self.store.update(extend)

    def change(self, mutation) -> dict:
        with self.lock:
            self._require_admin()
            updated = self.store.update(mutation)
        self.notify("changed")
        return updated

    def admin_action(self, action: str, pc_id: str | None = None,
                     **details) -> dict:
        self._require_admin()
        snap = self.snapshot()
        if action in ("request_unlock", "rename_pc") and not pc_id:
            raise ValueError("Choose a registered PC.")
        if pc_id and pc_id not in snap["members"]:
            raise ValueError("Unknown PC.")
        if action in ("start", "add", "end", "pause", "resume", "request_unlock") \
                and pc_id == snap["active_admin"].get("pc_id"):
            raise ValueError("The Admin PC is not a customer station.")
        if action in ("start", "add", "end", "pause", "resume"):
            if not pc_id:
                raise ValueError("Choose a registered User PC.")
            return self._remote_session_action(action, pc_id, details)

        def mutate(state):
            if action == "request_unlock":
                state["unlock_requests"][pc_id] = time.time()
            elif action == "rename_pc":
                name = details["name"].strip()
                if not name or len(name) > 40:
                    raise ValueError("PC name must be 1 to 40 characters.")
                state["members"][pc_id]["name"] = name
            elif action == "settings":
                state["cafe_name"] = details["cafe_name"].strip()
                if not state["cafe_name"]:
                    raise ValueError("Cafe name is required.")
                state["admin_name"] = details["admin_name"].strip()
                if not state["admin_name"]:
                    raise ValueError("Admin display name is required.")
                for field in ("developer_website", "developer_contact"):
                    value = details.get(field, "").strip()
                    if len(value) > 120:
                        raise ValueError("Developer website/contact must be 120 characters or less.")
                    state[field] = value
                grace_minutes = int(details["grace_minutes"])
                signout_minutes = int(details["auto_signout_minutes"])
                if not 1 <= grace_minutes <= 120 or not 1 <= signout_minutes <= 1440:
                    raise ValueError("Grace and sign-out delays are out of range.")
                state["settings"]["grace_minutes"] = grace_minutes
                state["settings"]["auto_signout_minutes"] = signout_minutes
                # Sign-out execution remains disabled pending a restart/recovery
                # design, as required by the product specification.
                state["settings"]["auto_signout_enabled"] = False
            elif action == "computer_sort":
                option = details.get("option")
                if option not in ("recent", "name"):
                    raise ValueError("Unknown computer sorting option.")
                state["settings"]["computer_sort"] = option
            elif action == "customer_access_settings":
                state["settings"]["retainer_restart_cooldown_minutes"] = \
                    validate_cooldown_minutes(details["retainer_minutes"])
                state["settings"]["customer_login_cooldown_minutes"] = \
                    validate_cooldown_minutes(details["login_minutes"])
            elif action == "password":
                state["admin_password"] = hash_password(details["new_password"])
                state["admin_signing"] = create_admin_signing_record(
                    details["new_password"])
                state.setdefault("admin_signing_keys", [snap["admin_signing"]["public"]])
                state["admin_signing_keys"].append(state["admin_signing"]["public"])
            elif action == "logo":
                state["logo"] = details["logo"]
            else:
                raise ValueError("Unsupported Admin action.")

        updated = self.change(mutate)
        if action == "password":
            # Peers should save the new public verifier before the next claim.
            self._replicate_once()
            self.admin_signing_key = unlock_admin_signing_key(
                details["new_password"], updated["admin_signing"]
            )
        self._reconcile_local_access()
        return updated

    def replace_session(self, pc_id: str, **details) -> dict:
        """Durably finish one ordinary session before starting its replacement."""
        snap = self.snapshot()
        current = snap["sessions"].get(pc_id)
        if not current:
            return self.admin_action("start", pc_id, **details)
        customer = self.customer_pc_states().get(pc_id, {})
        if customer.get("retainerActive"):
            raise RuntimeError(
                "Stop the registered customer's retainer session before replacement.")
        diagnostic_info("SESSION", "REPLACEMENT_STARTED",
                        pc=fingerprint(pc_id),
                        session=fingerprint(current.get("id")))
        self.admin_action("end", pc_id, reason="admin")
        if self.snapshot()["sessions"].get(pc_id):
            raise RuntimeError(
                "The previous session could not be confirmed ended; no new "
                "session was started.")
        result = self.admin_action("start", pc_id, **details)
        diagnostic_info("SESSION", "REPLACEMENT_COMPLETED",
                        pc=fingerprint(pc_id),
                        session=fingerprint(
                            result["sessions"].get(pc_id, {}).get("id")))
        return result

    def set_locked_wallpaper(self, content: bytes, media_type: str) -> dict:
        with self.lock:
            self._require_customer_admin()
            metadata = self.store.set_wallpaper(content, media_type)
        self.notify("changed")
        return metadata

    def reset_locked_wallpaper(self) -> None:
        with self.lock:
            self._require_customer_admin()
            self.store.reset_wallpaper()
        self.notify("changed")

    def _prepare_peer_authority(self, pc_id: str) -> tuple[dict, dict]:
        """Confirm identity/protocol, then refresh authority before any mutation."""
        peer = next((item for item in self.store.peers()
                     if item["pc_id"] == pc_id), None)
        if not peer or self.peer_status(pc_id) is None:
            raise RuntimeError(
                "Target PC is offline; session control is unavailable.")
        status = self.network.call(peer["ip"], "status", {})
        if status.get("pc_id") != pc_id:
            raise RuntimeError("Target PC identity did not match the selected computer.")
        if int(status.get("authority_protocol", 0)) != AUTHORITY_PROTOCOL:
            raise RuntimeError(
                "Target PC uses an incompatible Admin authority protocol. "
                "Update GameGrid on every cafe PC.")
        snap = self.snapshot()
        self.network.call(peer["ip"], "push_snapshot", {"snapshot": snap})
        records = self.network.call(peer["ip"], "session_records", {}).get(
            "records", [])
        for record in records:
            if record.get("payload", {}).get("pc_id") == pc_id:
                self.store.merge_owner_record(record)
        return peer, status

    @staticmethod
    def _command_applied(owner: dict | None, command_id: str) -> bool:
        if not owner:
            return False
        return (any(item.get("command", {}).get("id") == command_id
                    for item in owner.get("grants", [])) or
                (owner.get("history_clear") or {}).get(
                    "command", {}).get("id") == command_id)

    def _query_command_result(self, peer: dict, signed_command: dict) -> dict:
        command = signed_command["command"]
        current_term = int(self.snapshot()["active_admin"]["term"])
        reply = self.network.call(peer["ip"], "command_status", {
            "command_id": command["id"], "term": current_term})
        record = reply.get("record")
        if record:
            self.store.merge_owner_record(record)
        return reply

    def _resolve_pending_command(self, pc_id: str, peer: dict,
                                 requested_action: str,
                                 requested_details: dict) -> tuple[dict | None, bool]:
        """Return a reusable command and whether the requested action is complete."""
        snap = self.snapshot()
        pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
        raw = self.store.local(pending_key)
        if not raw:
            return None, False
        signed_command = json.loads(raw)
        previous = signed_command["command"]
        same_action = (previous["action"] == requested_action and
                       previous["details"] == requested_details)
        diagnostic_info("SESSION", "RECONCILIATION_STARTED",
                        pc=fingerprint(pc_id), op=fingerprint(previous["id"]),
                        action=previous["action"])
        reply = self._query_command_result(peer, signed_command)
        if reply.get("applied"):
            self.store.archive_session_command(
                signed_command, "applied", "confirmed by target owner record")
            self.store.delete_local(pending_key)
            self.pending_retry_state.pop(pc_id, None)
            diagnostic_info("SESSION", "RECONCILIATION_COMPLETED",
                            pc=fingerprint(pc_id), op=fingerprint(previous["id"]),
                            result="applied")
            return None, same_action
        if not reply.get("definitive"):
            diagnostic_warning("SESSION", "RECONCILIATION_BLOCKED",
                               pc=fingerprint(pc_id),
                               op=fingerprint(previous["id"]),
                               reason="target could not establish a durable result")
            raise RuntimeError(
                "The previous command result is still uncertain. GameGrid will "
                "retry reconciliation after the connection recovers.")
        current_claim = self.snapshot()["active_admin"]
        reusable = (same_action
                    and previous.get("admin_id") == self.store.pc_id
                    and int(previous.get("term", -1))
                    == int(current_claim.get("term", 0)))
        if reusable:
            diagnostic_info("SESSION", "COMMAND_RETRY_READY",
                            pc=fingerprint(pc_id), op=fingerprint(previous["id"]),
                            result="definitely_not_applied")
            return signed_command, False
        self.store.archive_session_command(
            signed_command, "not_applied",
            "target atomically confirmed absence; old authority command retired")
        self.store.delete_local(pending_key)
        self.pending_retry_state.pop(pc_id, None)
        diagnostic_info("SESSION", "RECONCILIATION_COMPLETED",
                        pc=fingerprint(pc_id), op=fingerprint(previous["id"]),
                        result="not_applied")
        return None, False

    def _schedule_pending_retry(self, pc_id: str) -> None:
        _next, attempts = self.pending_retry_state.get(pc_id, (0.0, 0))
        attempts += 1
        delay = min(60.0, float(3 * (2 ** min(attempts - 1, 4))))
        self.pending_retry_state[pc_id] = (time.monotonic() + delay, attempts)

    def _reconcile_pending_commands(self) -> None:
        """Resolve durable uncertain commands after restart or reconnection."""
        if not self.is_admin():
            return
        pool_id = self.store.current_pool_id or ""
        prefix = f"pending_session_command:{pool_id}:"
        for key, raw in self.store.local_items(prefix):
            pc_id = key[len(prefix):]
            due, _attempts = self.pending_retry_state.get(pc_id, (0.0, 0))
            if time.monotonic() < due or self.peer_status(pc_id) is None:
                continue
            try:
                signed_command = json.loads(raw)
                peer, _status = self._prepare_peer_authority(pc_id)
                result = self._query_command_result(peer, signed_command)
                if not (result.get("applied") or result.get("definitive")):
                    self._schedule_pending_retry(pc_id)
                    continue
                state = "applied" if result.get("applied") else "not_applied"
                reason = ("confirmed during automatic reconciliation" if
                          result.get("applied") else
                          "target atomically confirmed absence during recovery")
                self.store.archive_session_command(signed_command, state, reason)
                self.store.delete_local(key)
                self.pending_retry_state.pop(pc_id, None)
                diagnostic_info(
                    "SESSION", "AUTOMATIC_RECONCILIATION_COMPLETED",
                    pc=fingerprint(pc_id),
                    op=fingerprint(signed_command["command"]["id"]),
                    result=state,
                )
                self.notify("changed")
            except Exception as error:
                self._schedule_pending_retry(pc_id)
                diagnostic_warning(
                    "SESSION", "AUTOMATIC_RECONCILIATION_RETRY",
                    pc=fingerprint(pc_id), error_type=type(error).__name__,
                    reason=str(error),
                )

    def _remote_session_action(self, action: str, pc_id: str,
                               details: dict) -> dict:
        with self.lock:
            self._require_admin()
            snap = self.snapshot()
            customer_state = self.customer_pc_states().get(pc_id, {})
            if customer_state.get("retainerActive") and action in (
                    "start", "add", "pause", "resume"):
                raise RuntimeError(
                    "Stop this PC's retainer session before ordinary session controls."
                )
            # A failed prerequisite refresh must never create a blocking command.
            peer, _ = self._prepare_peer_authority(pc_id)
            pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
            signed_command, already_complete = self._resolve_pending_command(
                pc_id, peer, action, details)
            if already_complete:
                return self.snapshot()
            if signed_command is None:
                snap = self.snapshot()
                session = snap["sessions"].get(pc_id)
                command = {
                    "id": uuid.uuid4().hex, "pool_id": snap["pool_id"],
                    "admin_id": self.store.pc_id, "target": pc_id,
                    "term": snap["active_admin"]["term"], "action": action,
                    "session_id": uuid.uuid4().hex if action == "start" else
                                  None if action == "clear_history" else
                                  (session["id"] if session else None),
                    "details": details,
                }
                signed_command = {
                    "command": command,
                    "signature": sign_node(self.store.node_private_key(), command),
                    "admin_proof": sign_admin_proof(self.admin_signing_key, command),
                }
                self.store.set_local(pending_key, json.dumps(signed_command))
                diagnostic_info("SESSION", "COMMAND_CREATED",
                                pc=fingerprint(pc_id), op=fingerprint(command["id"]),
                                action=action, term=command["term"])
            command = signed_command["command"]
            try:
                diagnostic_info("SESSION", "COMMAND_SENT",
                                pc=fingerprint(pc_id), op=fingerprint(command["id"]),
                                action=command["action"])
                reply = self.network.call(
                    peer["ip"], "session_command", signed_command)
            except Exception as original_error:
                diagnostic_warning("SESSION", "COMMAND_DELIVERY_UNCERTAIN",
                                   pc=fingerprint(pc_id),
                                   op=fingerprint(command["id"]),
                                   action=command["action"],
                                   error_type=type(original_error).__name__,
                                   reason=str(original_error))
                try:
                    result = self._query_command_result(peer, signed_command)
                except Exception:
                    self._schedule_pending_retry(pc_id)
                    raise RuntimeError(
                        "Session command result is uncertain. GameGrid will "
                        "reconcile it automatically after reconnection.") from original_error
                if not result.get("applied"):
                    if result.get("definitive"):
                        self.store.archive_session_command(
                            signed_command, "not_applied",
                            f"target rejected command: {type(original_error).__name__}")
                        self.store.delete_local(pending_key)
                    raise original_error
                reply = {"target": pc_id, "saved": True,
                         "record": result.get("record"),
                         "access_ready": result.get("access_ready", False)}
            if reply.get("target") != pc_id or not reply.get("saved"):
                raise RuntimeError("Target did not confirm the local session write.")
            if reply.get("record"):
                self.store.merge_owner_record(reply["record"])
            self.store.archive_session_command(
                signed_command, "applied", "authenticated acknowledgment received")
            self.store.delete_local(pending_key)
            self.pending_retry_state.pop(pc_id, None)
            if action != "clear_history" and not reply.get("access_ready"):
                raise RuntimeError(
                    "Target saved the session command but could not confirm desktop access. "
                    "Check the User Console; the saved operation will not be repeated.")
            if action in ("start", "end"):
                self.change(lambda state: state["unlock_requests"].pop(pc_id, None))
            diagnostic_info("SESSION", "COMMAND_ACKNOWLEDGED",
                            pc=fingerprint(pc_id), op=fingerprint(command["id"]),
                            action=command["action"], result="applied")
            self.notify("changed")
            return self.snapshot()

    def clear_history(self, pc_id: str) -> dict:
        """Clear only one online owner's completed history via its signed record."""
        self._require_admin()
        snap = self.snapshot()
        if pc_id not in snap["members"]:
            raise ValueError("Choose a registered PC.")
        if pc_id != self.store.pc_id:
            status = self.peer_status(pc_id)
            if not status:
                raise RuntimeError("Target PC is offline; history cannot be cleared.")
            pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
            saved = self.store.local(pending_key)
            if saved and json.loads(saved)["command"]["action"] == "clear_history":
                details = json.loads(saved)["command"]["details"]
            else:
                peer = next(item for item in self.store.peers()
                            if item["pc_id"] == pc_id)
                records = self.network.call(peer["ip"], "session_records", {})["records"]
                for item in records:
                    if item["payload"]["pc_id"] == pc_id:
                        self.store.merge_owner_record(item)
                owner = next((item["payload"] for item in self.store.owner_records()
                              if item["payload"]["pc_id"] == pc_id), None)
                details = {"history_epoch": owner.get("history_epoch", 0) if owner else 0}
            return self._remote_session_action("clear_history", pc_id, details)
        owner = next((item["payload"] for item in self.store.owner_records()
                      if item["payload"]["pc_id"] == pc_id), None)
        details = {"history_epoch": owner.get("history_epoch", 0) if owner else 0}
        if pc_id == self.store.pc_id:
            command = {
                "id": uuid.uuid4().hex, "pool_id": snap["pool_id"],
                "admin_id": pc_id, "target": pc_id,
                "term": snap["active_admin"]["term"],
                "action": "clear_history", "session_id": None, "details": details,
            }
            signed = {"command": command,
                      "signature": sign_node(self.store.node_private_key(), command),
                      "admin_proof": sign_admin_proof(self.admin_signing_key, command)}
            self._apply_session_command(signed, pc_id)
            return self.snapshot()

    def _send_pairing_revocation(self, ip: str, snapshot: dict,
                                 secret: str, notice: dict) -> dict:
        """Deliver while both PCs can still authenticate the old membership."""
        return authenticated_call(
            ip, self.store.pc_id, snapshot, secret, "pairing_revoked",
            {"revocation": notice}, self.store.node_private_key())

    def remove_computer(self, pc_id: str) -> dict:
        """Revoke one idle User PC without deleting its historical records."""
        prepared_ip = ""
        notice: dict | None = None
        old_snapshot: dict | None = None
        secret = ""
        with self.lock:
            self._require_customer_admin()
            diagnostic_info("COMPUTER", "REMOVAL_REQUESTED",
                            pc=fingerprint(pc_id), forced=False)
            snap = self.snapshot()
            existing = snap.get("revocations", {}).get(pc_id)
            if pc_id not in snap["members"]:
                if existing:
                    return {"removed": True, "already_removed": True,
                            "pc_id": pc_id,
                            "pc_name": existing["certificate"]["pc_name"]}
                raise ValueError("Choose a registered User PC.")
            if pc_id == snap["active_admin"].get("pc_id"):
                raise ValueError("The active Admin PC cannot be removed.")
            member = dict(snap["members"][pc_id])
            peer = next((item for item in self.store.peers()
                         if item["pc_id"] == pc_id), None)
            if self.store.local(
                    f"pending_session_command:{snap['pool_id']}:{pc_id}") \
                    and peer and self.peer_status(pc_id):
                self._reconcile_pending_commands()
                snap = self.snapshot()
            if snap["sessions"].get(pc_id):
                raise RuntimeError(
                    "End this PC's active gaming session before removing it.")
            if self.store.customer_ownership(pc_id=pc_id):
                raise RuntimeError(
                    "Log out the registered customer on this PC before removing it.")
            pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
            if self.store.local(pending_key):
                raise RuntimeError(
                    "A session change is still pending for this PC. Retry or "
                    "resolve it before removal.")

            certificate = {
                "version": 1, "pool_id": snap["pool_id"], "pc_id": pc_id,
                "pc_name": member["name"],
                "public_key": member["public_key"],
                "authorization_id": member["authorization_id"],
                "revocation_id": uuid.uuid4().hex,
                "revision": int(snap["revision"]) + 1,
                "revoked_at": time.time(), "removed_by": self.store.pc_id,
            }
            notice = {
                "certificate": certificate,
                "signing_public": snap["admin_signing"]["public"],
                "signature": sign_admin_proof(
                    self.admin_signing_key, certificate),
            }
            verify_pairing_revocation(
                snap, notice, pc_id, member["public_key"],
                member["authorization_id"])

            if peer and self.peer_status(pc_id):
                self._prepare_peer_authority(pc_id)
                reply = self.network.call(peer["ip"],
                                          "prepare_pairing_removal",
                                          {"revocation": notice})
                if (reply.get("target") != pc_id
                        or not reply.get("ready")):
                    raise RuntimeError(
                        "Target PC did not confirm it is safe to remove.")
                prepared_ip = peer["ip"]

            try:
                # Recheck the authoritative state after the remote idle guard.
                snap = self.snapshot()
                if (snap["sessions"].get(pc_id)
                        or self.store.customer_ownership(pc_id=pc_id)
                        or self.store.local(pending_key)):
                    raise RuntimeError(
                        "The PC's session state changed during removal. Try again "
                        "after it is idle.")
                old_snapshot = snap
                secret = self.store.secret() or ""

                def revoke(state):
                    current = state["members"].get(pc_id)
                    if current is None:
                        return
                    if (current.get("public_key") != member["public_key"]
                            or current.get("authorization_id") !=
                            member["authorization_id"]):
                        raise RuntimeError(
                            "The PC pairing changed during removal. Refresh and retry.")
                    state["members"].pop(pc_id)
                    state["sessions"].pop(pc_id, None)
                    state["unlock_requests"].pop(pc_id, None)
                    state.setdefault("revocations", {})[pc_id] = notice

                updated = self.store.update(revoke)
            except Exception:
                if prepared_ip:
                    try:
                        self.network.call(
                            prepared_ip, "cancel_pairing_removal",
                            {"revocation_id": certificate["revocation_id"]})
                    except Exception:
                        pass
                raise

        self.store.forget_peer(pc_id)
        with self.lock:
            self.peer_statuses.pop(pc_id, None)
            self.connection_states.pop(pc_id, None)
            self.graceful_disconnects.discard(pc_id)
        if prepared_ip and old_snapshot:
            try:
                self._send_pairing_revocation(
                    prepared_ip, old_snapshot, secret, notice)
            except Exception:
                # The durable certificate is returned when the old PC retries.
                pass
        self.notify("changed")
        self._replicate_once()
        diagnostic_info("COMPUTER", "REMOVAL_COMPLETED",
                        pc=fingerprint(pc_id), forced=False,
                        revision=updated["revision"])
        return {"removed": True, "already_removed": False,
                "pc_id": pc_id, "pc_name": member["name"],
                "revision": updated["revision"]}

    def force_remove_computer(self, pc_id: str) -> dict:
        """Revoke an unreachable identity while preserving unresolved evidence."""
        with self.lock:
            self._require_customer_admin()
            diagnostic_warning("COMPUTER", "FORCE_REMOVE_CONFIRMED",
                               pc=fingerprint(pc_id))
            snap = self.snapshot()
            if pc_id not in snap["members"]:
                raise ValueError("Choose a registered User PC.")
            if pc_id == snap["active_admin"].get("pc_id"):
                raise ValueError("The active Admin PC cannot be removed.")
            member = dict(snap["members"][pc_id])
            peer = next((item for item in self.store.peers()
                         if item["pc_id"] == pc_id), None)
            if peer:
                try:
                    status = self.network.call(peer["ip"], "status", {})
                    if status.get("pc_id") == pc_id:
                        self.store.note_peer(pc_id, peer["ip"])
                        self.peer_statuses[pc_id] = (time.time(), status)
                        raise RuntimeError(
                            "This computer is reachable. Its state was refreshed; "
                            "use normal Remove Computer after active gameplay ends.")
                except RuntimeError:
                    raise
                except Exception:
                    pass

            pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
            pending_raw = self.store.local(pending_key)
            pending = json.loads(pending_raw) if pending_raw else None
            owner_record = next((item for item in self.store.owner_records()
                                 if item["payload"]["pc_id"] == pc_id), None)
            ownership = self.store.customer_ownership(pc_id=pc_id)
            certificate = {
                "version": 1, "pool_id": snap["pool_id"], "pc_id": pc_id,
                "pc_name": member["name"], "public_key": member["public_key"],
                "authorization_id": member["authorization_id"],
                "revocation_id": uuid.uuid4().hex,
                "revision": int(snap["revision"]) + 1,
                "revoked_at": time.time(), "removed_by": self.store.pc_id,
            }
            notice = {
                "certificate": certificate,
                "signing_public": snap["admin_signing"]["public"],
                "signature": sign_admin_proof(
                    self.admin_signing_key, certificate),
            }
            verify_pairing_revocation(
                snap, notice, pc_id, member["public_key"],
                member["authorization_id"])
            evidence = {
                "member": member,
                "session": snap["sessions"].get(pc_id),
                "owner_record": owner_record,
                "pending_command": pending,
                "customer_ownership": ownership,
                "revocation": notice,
                "warning": ("The offline PC was not contacted; lock and session "
                            "completion were not confirmed."),
            }

            updated = self.store.commit_forced_removal(
                pc_id, member["authorization_id"], notice, evidence, pending)
            self.store.forget_peer(pc_id)
            self.peer_statuses.pop(pc_id, None)
            self.connection_states.pop(pc_id, None)
            self.peer_failures.pop(pc_id, None)
            diagnostic_warning(
                "COMPUTER", "FORCE_REMOVE_COMPLETED",
                pc=fingerprint(pc_id), name=member["name"],
                had_session=bool(evidence["session"]),
                had_pending=bool(pending), had_customer=bool(ownership),
                revision=updated["revision"],
            )
        self.notify("changed")
        self._replicate_once()
        return {"removed": True, "forced": True, "pc_id": pc_id,
                "pc_name": member["name"], "revision": updated["revision"]}

    def remote_exit(self, pc_id: str) -> dict:
        self._require_admin()
        snap = self.snapshot()
        if pc_id == self.store.pc_id or pc_id not in snap["members"]:
            raise ValueError("Choose a registered User PC.")
        try:
            peer, _status = self._prepare_peer_authority(pc_id)
            pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
            if self.store.local(pending_key):
                self._resolve_pending_command(pc_id, peer, "remote_exit", {})
                snap = self.snapshot()
            reply = self.network.call(peer["ip"], "remote_exit",
                                      {"term": snap["active_admin"]["term"]})
        except Exception as error:
            raise RuntimeError(
                "Could not confirm remote exit. The target may still be closing; "
                "check its online status before retrying. " + str(error)
            ) from error
        if (reply.get("target") != pc_id or not reply.get("accepted")
                or not reply.get("session_finalized")):
            raise RuntimeError("Target did not confirm session finalization and exit acceptance.")
        self._record_graceful_disconnect(pc_id)
        # The process is still sending this reply; offline status follows disconnect.
        return reply

    def submit_customer_action(self, action: str, **details) -> None:
        """Ask the active Admin to perform a customer-originated write."""
        if action == "end":
            self.finish_local_session("customer")
            return
        if action == "rename":
            raise ValueError("Guest and registered customer names cannot be edited here.")
        if self.is_admin():
            self._customer_action(action, details, self.store.pc_id)
            return
        ip = self.active_admin_ip()
        if not ip:
            raise RuntimeError("Admin is offline. Try again when it reconnects.")
        self.network.call(ip, "customer_action",
                          {"action": action, "details": details})

    def local_staff_session_action(self, action: str, password: str,
                                   operation_id: str, **details) -> dict:
        """Apply a password-authorized local grant without claiming Admin."""
        snap = self.snapshot()
        if not snap or not verify_password(password, snap.get("admin_password", {})):
            raise PermissionError("Incorrect Admin password.")
        active = snap["active_admin"]
        remote_admin = active.get("pc_id")
        if remote_admin and remote_admin != self.store.pc_id:
            ip = self.active_admin_ip()
            status = self.peer_status(remote_admin)
            if ip:
                try:
                    fresh = self.network.call(ip, "status", {})
                    if (fresh.get("pc_id") == remote_admin
                            and fresh.get("admin")
                            and int(fresh.get("admin_term", -1))
                            == int(active.get("term", 0))):
                        if int(fresh.get("authority_protocol", 0)) \
                                != AUTHORITY_PROTOCOL:
                            raise RuntimeError(
                                "Admin is reachable but uses an incompatible "
                                "authority protocol. Update every cafe PC.")
                        self.peer_statuses[remote_admin] = (time.time(), fresh)
                        self._note_admin_claim(
                            remote_admin, int(active.get("term", 0)))
                        status = fresh
                except RuntimeError:
                    raise
                except Exception:
                    pass
            if (status and status.get("admin")
                    and status.get("pc_id") == remote_admin
                    and int(status.get("admin_term", -1))
                    == int(active.get("term", 0))):
                raise RuntimeError(
                    "Admin is online. Start or change this session from the "
                    "Admin Dashboard.")
        signing_key = unlock_admin_signing_key(password, snap["admin_signing"])
        if action not in ("start", "add") or len(operation_id) != 32:
            raise ValueError("Invalid local Staff operation.")

        with self.lock:
            if self.local_shutdown_pending:
                raise RuntimeError("This PC is closing; session changes are unavailable.")
            self._require_pairing_available()
            snap = self.snapshot()
            session, history, grants = self._owner_payload()
            if any(item["command"]["id"] == operation_id for item in grants):
                return next(item for item in self.store.owner_records()
                            if item["payload"]["pc_id"] == self.store.pc_id)
            command_details: dict
            if action == "start":
                if session:
                    raise ValueError("This PC already has a session.")
                if (self.store.local_customer_session() or {}).get(
                        "retainer_active"):
                    raise RuntimeError(
                        "Stop retainer play before starting an ordinary session."
                    )
                kind = str(details.get("kind", ""))
                paid, buffer = validate_start(
                    kind, str(details.get("paid_minutes", "")),
                    str(details.get("buffer_minutes", "")))
                session = sessions.start_session(kind, paid, buffer)
                customer = self.store.local_customer_session()
                if customer:
                    session["player"] = customer["name"]
                command_details = {"kind": kind, "paid_minutes": paid,
                                   "buffer_minutes": buffer,
                                   "source": "local_staff"}
                session_id = session["id"]
            else:
                if not session:
                    raise ValueError("This PC has no active session.")
                minutes = validate_add(int(details.get("minutes", 0)))
                sessions.add_paid_time(
                    session, minutes,
                    grace_minutes=snap["settings"]["grace_minutes"])
                command_details = {"minutes": minutes,
                                   "source": "local_staff"}
                session_id = session["id"]
            command = {
                "id": operation_id, "pool_id": snap["pool_id"],
                "admin_id": self.store.pc_id, "target": self.store.pc_id,
                "term": int(snap["active_admin"].get("term", 0)),
                "action": action, "session_id": session_id,
                "details": command_details,
            }
            grant = {"command": command,
                     "signature": sign_node(self.store.node_private_key(), command),
                     "admin_proof": sign_admin_proof(signing_key, command)}
            grants.append(grant)
            record = self._save_owner_state(session, history, grants)
            self._reconcile_local_access()
            return record

    def _customer_action(self, action: str, details: dict, sender: str) -> None:
        self._require_admin()
        if action == "end":
            self.admin_action("end", sender, reason="customer")
        elif action == "rename":
            raise ValueError("Player names are saved on the User PC itself.")
        elif action == "request_unlock":
            self.admin_action("request_unlock", sender)
        else:
            raise ValueError("Unsupported customer action.")

    def _apply_session_command(self, data: dict, sender: str) -> dict:
        """Only the currently claimed Admin can mutate this PC's session."""
        with self.lock:
            if self.local_shutdown_pending:
                raise RuntimeError("This PC is closing; session changes are unavailable.")
            if data.get("command", {}).get("action") in (
                    "start", "add", "pause", "resume"):
                self._require_pairing_available()
            snap = self.snapshot()
            command = data["command"]
            diagnostic_info("SESSION", "COMMAND_RECEIVED",
                            pc=fingerprint(self.store.pc_id),
                            admin=fingerprint(sender),
                            op=fingerprint(command.get("id")),
                            action=command.get("action"),
                            term=command.get("term"))
            claim = self._require_remote_admin(sender, command.get("term"))
            if (sender != command["admin_id"]
                    or command["pool_id"] != snap["pool_id"]
                    or command["target"] != self.store.pc_id):
                raise PermissionError("Only the current Admin can control this session.")
            verify_node(snap["members"][sender]["public_key"], command,
                        data["signature"])
            verify_admin_proof(snap["admin_signing"]["public"], command,
                               data["admin_proof"])
            session, history, grants = self._owner_payload()
            action, details = command["action"], command["details"]
            if action == "clear_history":
                previous = next((item for item in self.store.owner_records()
                                 if item["payload"]["pc_id"] == self.store.pc_id), None)
                last = (previous["payload"].get("history_clear") or {}) if previous else {}
                if last.get("command", {}).get("id") == command["id"]:
                    return {"target": self.store.pc_id, "saved": True,
                            "record": previous, "access_ready": True}
                epoch = previous["payload"].get("history_epoch", 0) if previous else 0
                if details.get("history_epoch") != epoch:
                    raise ValueError("History changed; refresh and confirm again.")
                record = self._save_owner_state(session, [], grants,
                                                clear_command=data)
                return {"target": self.store.pc_id, "saved": True,
                        "record": record, "access_ready": True}
            if action == "end" and not session and command["session_id"] is None:
                self._reconcile_local_access()
                return {"target": self.store.pc_id, "saved": True,
                        "record": None, "access_ready": self._local_access_ready()}
            if any(item["command"]["id"] == command["id"] for item in grants):
                record = next(item for item in self.store.owner_records()
                              if item["payload"]["pc_id"] == self.store.pc_id)
                return {"target": self.store.pc_id, "saved": True,
                        "record": record, "access_ready": self._local_access_ready()}
            if action == "start":
                if session:
                    raise ValueError("This PC already has a session.")
                customer = self.store.local_customer_session()
                if (customer or {}).get("retainer_active"):
                    raise RuntimeError(
                        "Stop retainer play before starting an ordinary session."
                    )
                session = sessions.start_session(details["kind"],
                    int(details.get("paid_minutes", 0)),
                    int(details.get("buffer_minutes", 0)))
                session["id"] = command["session_id"]
                if customer:
                    session["player"] = customer["name"]
            else:
                if not session or command["session_id"] != session["id"]:
                    raise ValueError("Session has changed; refresh the dashboard.")
                if action == "add":
                    sessions.add_paid_time(session, int(details["minutes"]),
                        grace_minutes=snap["settings"]["grace_minutes"])
                elif action == "pause":
                    sessions.pause_session(session,
                        grace_minutes=snap["settings"]["grace_minutes"])
                elif action == "resume":
                    sessions.resume_session(session)
                elif action == "end":
                    now = time.time()
                    record = sessions.finish_session(session, self.store.pc_id,
                        snap["members"][self.store.pc_id]["name"],
                        details.get("reason", "admin"), now)
                    history.append(record)
                    diagnostic_info(
                        "SESSION", "SESSION_COMPLETED",
                        pc=fingerprint(self.store.pc_id),
                        session=fingerprint(record["id"]),
                        reason=record.get("reason"),
                        duration_seconds=record.get("counted_seconds", 0),
                        result="persisting")
                    session = None
                else:
                    raise ValueError("Unsupported session command.")
            grants.append(data)
            record = self._save_owner_state(session, history, grants)
            self._reconcile_local_access()
            diagnostic_info("SESSION", "COMMAND_SAVED",
                            pc=fingerprint(self.store.pc_id),
                            op=fingerprint(command["id"]), action=action,
                            session=fingerprint(command.get("session_id")))
            return {"target": self.store.pc_id, "saved": True,
                    "record": record, "access_ready": self._local_access_ready()}

    def handle_plain(self, operation: str, data: dict, ip: str) -> dict:
        """Only join workflow is public; it never discloses the pool secret raw."""
        self._require_admin()
        snap = self.snapshot()
        if operation == "join_request":
            if data.get("pool_id") != snap["pool_id"]:
                raise ValueError("Join request is for another cafe.")
            request_id = str(data.get("request_id", ""))
            pc_id = str(data.get("pc_id", ""))
            name = str(data.get("name", ""))[:40]
            public_key = str(data.get("public_key", ""))
            pairing_public = str(data.get("pairing_public", ""))
            verification_code = str(data.get("verification_code", ""))
            cancel_token = str(data.get("cancel_token", ""))
            if (len(request_id) != 32 or len(pc_id) != 32
                    or len(public_key) != 64 or len(pairing_public) != 64
                    or not re.fullmatch(r"\d{6}", verification_code)
                    or not re.fullmatch(r"[0-9a-f]{64}", cancel_token)
                    or not name.strip()):
                raise ValueError("Invalid join request.")
            bytes.fromhex(public_key)
            bytes.fromhex(pairing_public)
            private, admin_public = create_pairing_key()
            shared = pairing_secret(private, pairing_public)
            verification_digest = hmac.new(
                shared,
                f"verify:{request_id}:{verification_code}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            cancel_digest = hmac.new(
                shared,
                f"cancel:{request_id}:{cancel_token}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            self.store.record_join(request_id, pc_id, name, ip, public_key,
                                   pairing_public, admin_public, shared.hex(),
                                   verification_digest, cancel_digest)
            self.notify("join_request")
            return {"state": "pending", "pairing_admin_public": admin_public}
        if operation == "join_poll":
            request_id = str(data.get("request_id", ""))
            decision = self.store.join_decision(request_id)
            if not decision:
                raise ValueError("Unknown join request.")
            return decision
        if operation == "join_cancel":
            cancelled = self.store.cancel_join(
                str(data.get("request_id", "")),
                str(data.get("pc_id", "")),
                str(data.get("cancel_token", "")),
            )
            return {"state": "cancelled" if cancelled else "inactive"}
        raise ValueError("Unsupported public request.")

    def approve_join(self, request_id: str, pc_id: str,
                     verification_code: str, name: str) -> None:
        self._require_admin()
        pending = next((item for item in self.store.pending_joins()
                        if item["request_id"] == request_id and item["pc_id"] == pc_id), None)
        if not pending:
            raise ValueError("Join request is no longer pending.")
        if pc_id in self.snapshot()["members"]:
            raise ValueError("This computer is already registered.")
        shared = bytes.fromhex(self.store.verify_join_code(
            request_id, pc_id, verification_code.strip()
        ))
        name = name.strip()
        if not name or len(name) > 40:
            raise ValueError("PC name must be 1 to 40 characters.")
        updated = self.change(lambda state: state["members"].update(
            {pc_id: {"name": name, "public_key": pending["public_key"],
                     "authorization_id": secrets.token_hex(32),
                     "last_connected_at": time.time()}}
        ))
        welcome = {"snapshot": updated, "secret": self.store.secret(),
                   "pc_id": pc_id}
        self.store.decide_join(request_id, "accepted",
                               seal_welcome(shared, request_id, welcome))
        self.store.remember_address(pc_id, pending["ip"])
        diagnostic_info("PAIRING", "PAIRING_APPROVED",
                        pc=fingerprint(pc_id), name=name, ip=pending["ip"])

    def reject_join(self, request_id: str) -> None:
        self._require_admin()
        self.store.decide_join(request_id, "rejected")

    def handle_member(self, operation: str, data: dict,
                      sender: str, ip: str) -> dict:
        snap = self.snapshot()
        if operation == "status":
            return self.status()
        if operation == "snapshot":
            return {"snapshot": snap}
        if operation == "session_records":
            return {"records": self.store.owner_records()}
        if operation == "command_status":
            with self.lock:
                self._require_remote_admin(sender, data.get("term"))
                command_id = str(data.get("command_id", ""))
                if len(command_id) != 32:
                    raise ValueError("Invalid session command identifier.")
                record = next((item for item in self.store.owner_records()
                               if item["payload"]["pc_id"] == self.store.pc_id), None)
                owner = record["payload"] if record else None
                applied = self._command_applied(owner, command_id)
                return {"target": self.store.pc_id, "applied": applied,
                        "definitive": True, "record": record,
                        "access_ready": self._local_access_ready()}
        if operation == "session_record":
            record = data["record"]
            if record["payload"]["pc_id"] != sender:
                raise PermissionError("Only an owner can publish its session record.")
            return {"applied": self.store.merge_owner_record(record)}
        if operation == "graceful_shutdown":
            self._require_admin()
            if sender == self.store.pc_id or sender not in snap["members"]:
                raise PermissionError("Only a registered User PC can close remotely.")
            self._record_graceful_disconnect(sender)
            return {"recorded": True}
        if operation == "session_command":
            return self._apply_session_command(data, sender)
        if operation == "prepare_pairing_removal":
            with self.lock:
                snap = self.snapshot()
                claim = self._require_remote_admin(sender)
                member = snap["members"].get(self.store.pc_id)
                notice = data.get("revocation")
                if not member:
                    raise PermissionError(
                        "Only the active Admin can prepare a PC removal.")
                certificate = verify_pairing_revocation(
                    snap, notice, self.store.pc_id,
                    member.get("public_key"),
                    member.get("authorization_id"))
                if certificate.get("removed_by") != sender:
                    raise PermissionError(
                        "Pairing removal was not issued by the active Admin.")
                existing = self.pairing_removal
                if (existing and existing.get("revocation_id") !=
                        certificate["revocation_id"]
                        and existing.get("expires_at", 0) > time.time()):
                    raise RuntimeError("Another pairing removal is in progress.")
                if (snap["sessions"].get(self.store.pc_id)
                        or self.store.local_customer_session()
                        or self.retainer_unlock_pending
                        or self.local_shutdown_pending):
                    raise RuntimeError(
                        "This PC has active gameplay or a pending transition.")
                self.pairing_removal = {
                    "revocation_id": certificate["revocation_id"],
                    "expires_at": time.time() + PAIRING_REMOVAL_SECONDS,
                }
                return {"ready": True, "target": self.store.pc_id}
        if operation == "cancel_pairing_removal":
            self._require_remote_admin(sender)
            removal = self.pairing_removal
            if (removal and removal.get("revocation_id") ==
                    data.get("revocation_id")):
                self.pairing_removal = None
            return {"cancelled": True, "target": self.store.pc_id}
        if operation == "pairing_revoked":
            self._require_remote_admin(sender)
            member = snap["members"].get(self.store.pc_id)
            if not member:
                raise PermissionError(
                    "Only the active Admin can revoke this pairing.")
            certificate = verify_pairing_revocation(
                snap, data.get("revocation"), self.store.pc_id,
                member.get("public_key"), member.get("authorization_id"))
            if certificate.get("removed_by") != sender:
                raise PermissionError(
                    "Pairing removal was not issued by the active Admin.")
            if (snap["sessions"].get(self.store.pc_id)
                    or self.store.local_customer_session()
                    or self.retainer_unlock_pending):
                raise RuntimeError(
                    "Pairing removal cannot interrupt active gameplay.")
            return {"accepted": True, "target": self.store.pc_id}
        if operation == "remote_exit":
            self._require_remote_admin(sender, data.get("term"))
            if self.store.pc_id == sender:
                raise PermissionError("Only the active Admin can exit a User PC.")
            self.prepare_local_shutdown()
            return {"accepted": True, "target": self.store.pc_id,
                    "session_finalized": self.snapshot()["sessions"].get(self.store.pc_id) is None}
        if operation == "lease_challenge":
            challenge = uuid.uuid4().hex
            with self.lock:
                now = time.time()
                self.lease_challenges = {
                    key: expiry for key, expiry in self.lease_challenges.items()
                    if expiry > now
                }
                self.lease_challenges[(sender, challenge)] = now + 10
            return {"challenge": challenge}
        if operation == "prepare_admin_transfer":
            with self.lock:
                self._require_admin()
                snap = self.snapshot()
                proof = data.get("proof", {})
                if (proof.get("candidate") != sender
                        or proof.get("pool_id") != snap["pool_id"]
                        or int(proof.get("term", 0)) <= snap["active_admin"]["term"]
                        or snap["sessions"].get(sender)
                        or self.store.customer_ownerships()):
                    raise PermissionError("PC is not eligible for Admin transfer.")
                transfer_id = str(proof.get("transfer_id", ""))
                recipient_public = str(proof.get("recipient_public", ""))
                expected_context = {
                    "pool_id": snap["pool_id"],
                    "source_admin": self.store.pc_id,
                    "candidate": sender, "term": int(proof["term"]),
                    "transfer_id": transfer_id,
                    "challenge": proof.get("challenge"),
                }
                existing = self.handoff_transfer
                if (existing and existing.get("transfer_id") == transfer_id
                        and existing.get("candidate") == sender
                        and existing.get("expires_at", 0) > time.time()):
                    return {"manifest": existing["manifest"]}
                if existing:
                    raise RuntimeError("Another Admin transfer is already in progress.")
                if (len(transfer_id) != 32 or len(recipient_public) != 64
                        or set(proof) != set(expected_context) | {"recipient_public"}):
                    raise ValueError("Admin handoff proof is invalid.")
                challenge = proof.get("challenge")
                expiry = self.lease_challenges.pop((sender, challenge), 0)
                if expiry <= time.time():
                    raise PermissionError("Admin transfer challenge expired.")
                verify_admin_proof(snap["admin_signing"]["public"], proof,
                                   data.get("signature", ""))
                # Install the barrier before reading any authoritative tables.
                self.handoff_transfer = {
                    "transfer_id": transfer_id, "candidate": sender,
                    "term": int(proof["term"]), "expires_at": time.time()
                    + HANDOFF_EXPIRY_SECONDS, "manifest": None,
                    "chunks": [], "state_digest": "",
                    "committed_snapshot": None,
                }
                try:
                    bundle = self.store.authoritative_handoff_bundle(
                        self.store.pc_id, sender, int(proof["term"]),
                        transfer_id)
                    plaintext_size = len(json.dumps(
                        bundle, sort_keys=True,
                        separators=(",", ":")).encode("utf-8"))
                    if plaintext_size > MAX_HANDOFF_PLAINTEXT:
                        raise ValueError(
                            "Admin handoff data exceeds the safe transfer limit.")
                    package = seal_admin_handoff(
                        recipient_public, expected_context, bundle)
                    ciphertext = package.pop("ciphertext")
                    chunks = [
                        ciphertext[index:index + HANDOFF_CHUNK_HEX_LENGTH]
                        for index in range(
                            0, len(ciphertext), HANDOFF_CHUNK_HEX_LENGTH)
                    ]
                    manifest = {
                        "context": expected_context,
                        "sender_public": package["sender_public"],
                        "nonce": package["nonce"],
                        "chunk_count": len(chunks),
                        "ciphertext_bytes": len(ciphertext) // 2,
                        "ciphertext_digest": hashlib.sha256(
                            bytes.fromhex(ciphertext)).hexdigest(),
                        "state_digest": bundle["state_digest"],
                    }
                    self.handoff_transfer.update({
                        "expires_at": time.time() + HANDOFF_EXPIRY_SECONDS,
                        "manifest": manifest, "chunks": chunks,
                        "state_digest": bundle["state_digest"],
                    })
                except Exception:
                    self.handoff_transfer = None
                    raise
                return {"manifest": manifest}
        if operation == "admin_transfer_chunk":
            with self.lock:
                self._require_admin(allow_handoff=True)
                transfer = self.handoff_transfer or {}
                index = data.get("index")
                if (transfer.get("candidate") != sender
                        or transfer.get("transfer_id") != data.get("transfer_id")
                        or transfer.get("expires_at", 0) <= time.time()
                        or isinstance(index, bool) or not isinstance(index, int)
                        or not 0 <= index < len(transfer.get("chunks", []))):
                    raise PermissionError("Admin handoff chunk request is invalid.")
                return {"chunk": transfer["chunks"][index]}
        if operation == "commit_admin_transfer":
            with self.lock:
                transfer_id = str(data.get("transfer_id", ""))
                current = self.snapshot()
                active = current["active_admin"]
                if (active.get("pc_id") == sender
                        and active.get("handoff_id") == transfer_id):
                    return {"snapshot": current}
                self._require_admin(allow_handoff=True)
                transfer = self.handoff_transfer or {}
                claim = data.get("claim", {})
                if (transfer.get("candidate") != sender
                        or transfer.get("transfer_id") != transfer_id
                        or transfer.get("expires_at", 0) <= time.time()
                        or transfer.get("state_digest") != data.get("state_digest")
                        or claim != {"pool_id": current["pool_id"],
                                     "candidate": sender,
                                     "term": transfer.get("term")}
                        or self.store.customer_ownerships()):
                    raise PermissionError("Admin handoff is not ready to commit.")
                verify_admin_proof(current["admin_signing"]["public"], claim,
                                   data.get("claim_signature", ""))

                def commit(state):
                    if state["active_admin"].get("pc_id") != self.store.pc_id:
                        raise RuntimeError("Admin authority changed before commit.")
                    state["active_admin"] = {
                        "pc_id": sender, "term": transfer["term"],
                        "expires_at": time.time() + LEASE_SECONDS,
                        "proof": data["claim_signature"],
                        "handoff_id": transfer_id,
                    }

                committed = self.store.update(commit)
                transfer["committed_snapshot"] = committed
                self.handoff_deadline = time.monotonic() + 30
                self.store.set_local(self._lease_key("handed_off"),
                                     str(active["term"]))
                self.store.set_local(self._lease_key("until"), "0")
                self.admin_ui_open = False
                self.notify("role_lost")
                return {"snapshot": committed}
        if operation == "abort_admin_transfer":
            with self.lock:
                transfer = self.handoff_transfer
                if not transfer:
                    return {"aborted": True}
                if (transfer.get("candidate") != sender
                        or transfer.get("transfer_id") != data.get("transfer_id")):
                    raise PermissionError("Admin handoff abort is invalid.")
                if transfer.get("committed_snapshot") is not None:
                    return {"aborted": False}
                self.handoff_transfer = None
                return {"aborted": True}
        if operation == "push_snapshot":
            incoming = data.get("snapshot")
            if not isinstance(incoming, dict) or incoming.get("pool_id") != snap["pool_id"]:
                raise ValueError("Invalid pool snapshot.")
            if int(incoming.get("authority_protocol", 0)) != AUTHORITY_PROTOCOL:
                raise RuntimeError(
                    "Incompatible Admin authority protocol; update every cafe PC.")
            applied = self.accept_admin_snapshot(incoming, sender)
            claim = incoming["active_admin"]
            self._note_admin_claim(sender, int(claim["term"]))
            self.notify("changed")
            self._reconcile_local_access()
            return {"revision": self.snapshot()["revision"], "applied": applied,
                    "admin_rank": admin_rank(self.snapshot()),
                    "access_ready": self._local_access_ready(),
                    "wallpaper_id": self.store.current_wallpaper_id()}
        if operation == "wallpaper_sync":
            self._require_remote_admin(sender)
            metadata = data.get("metadata")
            encoded = data.get("content")
            if not isinstance(metadata, dict) or not isinstance(encoded, str):
                raise ValueError("Invalid wallpaper synchronization payload.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("Invalid wallpaper image encoding.") from error
            wallpaper_id = self.store.apply_wallpaper(metadata, content)
            self.notify("changed")
            return {"wallpaper_id": wallpaper_id}
        if operation == "customer_sync":
            self._require_remote_admin(sender)
            accounts = data.get("accounts", [])
            tombstones = data.get("tombstones", [])
            if not isinstance(accounts, list) or not isinstance(tombstones, list):
                raise ValueError("Invalid customer synchronization payload.")
            acknowledgments = self.store.apply_customer_replica(
                accounts, tombstones)
            self.notify("changed")
            return {"acknowledgments": acknowledgments}
        if operation == "customer_access":
            with self.lock:
                return self._handle_customer_access(data, sender)
        if operation == "retainer_control":
            self._require_remote_admin(sender)
            self._apply_retainer_control(data)
            return {"target": self.store.pc_id,
                    "locked": not self._local_access_ready()}
        if operation == "customer_action":
            self._customer_action(data["action"], data.get("details", {}), sender)
            return {"revision": self.snapshot()["revision"]}
        raise ValueError("Unsupported member request.")

    def prepare_join_request(self, pool_id: str, requested_name: str) -> dict:
        """Create the visible code locally before any network round trip."""
        private, public = create_pairing_key()
        request_id = uuid.uuid4().hex
        code = f"{secrets.randbelow(1_000_000):06d}"
        cancel_token = secrets.token_hex(32)
        return {
            "request_id": request_id, "private": private, "code": code,
            "cancel_token": cancel_token,
            "payload": {"pool_id": pool_id, "pc_id": self.store.pc_id,
                        "request_id": request_id, "name": requested_name,
                        "public_key": self.store.node_public_key(),
                        "pairing_public": public,
                        "verification_code": code,
                        "cancel_token": cancel_token},
        }

    def send_join_request(self, ip: str, request: dict) -> None:
        reply = plain_call(ip, "join_request", request["payload"])
        request["admin_public"] = reply["pairing_admin_public"]

    def request_join(self, ip: str, pool_id: str, requested_name: str):
        request = self.prepare_join_request(pool_id, requested_name)
        self.send_join_request(ip, request)
        return request

    def cancel_join(self, ip: str, request_id: str, cancel_token: str) -> None:
        plain_call(ip, "join_cancel", {
            "request_id": request_id, "pc_id": self.store.pc_id,
            "cancel_token": cancel_token,
        })

    def finish_join(self, ip: str, request_id: str, pairing_private,
                    old_pool_id: str | None = None) -> tuple[str, str | None]:
        reply = plain_call(ip, "join_poll", {"request_id": request_id})
        if reply["state"] == "pending":
            return "pending", None
        if reply["state"] in ("rejected", "expired", "cancelled"):
            return reply["state"], None
        shared = pairing_secret(pairing_private, reply["pairing_admin_public"])
        welcome = open_welcome(shared, request_id, reply["sealed_welcome"])
        snap = welcome["snapshot"]
        secret = welcome["secret"]
        if welcome["pc_id"] != self.store.pc_id or self.store.pc_id not in snap["members"]:
            raise PermissionError("Welcome package is for a different PC.")
        if snap["members"][self.store.pc_id].get("public_key") != self.store.node_public_key():
            raise PermissionError("Welcome package does not match this PC's key.")
        # Verify the destination while the former membership still exists.
        authenticated_call(ip, self.store.pc_id, snap, secret, "status", {},
                           self.store.node_private_key())
        self.store.save_pool(snap, secret, join=True)
        if old_pool_id and old_pool_id != snap["pool_id"]:
            self.store.leave_old_pool(old_pool_id)
            with self.lock:
                self.peer_statuses.clear()
            self.last_access_allowed = None
        self.store.note_peer(snap["active_admin"]["pc_id"], ip)
        try:
            self.network.call(ip, "snapshot", {})
        except Exception:
            pass
        self.notify("joined")
        return "accepted", None

    def _discover_peers_once(self) -> list[dict]:
        snap = self.snapshot()
        if not snap:
            return discover()
        found = discover()
        for item in found:
            if (item["pool_id"] == snap["pool_id"]
                    and (item["pc_id"] in snap["members"]
                         or item["pc_id"] == item.get("active_admin"))
                    and item["pc_id"] != self.store.pc_id):
                self.store.remember_address(item["pc_id"], item["ip"])
                if item["pc_id"] not in snap["members"]:
                    try:
                        # A stale removed PC may not know a later Admin Node,
                        # but its signed old request can receive the durable
                        # revocation certificate. Discovery alone grants nothing.
                        self.network.call(item["ip"], "status", {})
                    except Exception:
                        pass
                    if not self.store.current_pool_id:
                        return found
        return found

    def _replicate_once(self) -> None:
        if not self.is_admin():
            return
        snap = self.snapshot()
        peers = [peer for peer in self.store.peers()
                 if peer["pc_id"] in snap["members"]
                 and peer["pc_id"] != self.store.pc_id]
        if not peers:
            return
        customer_payload = self.store.customer_replica_payload()
        wallpaper = self.store.wallpaper_content()

        def push(peer: dict) -> tuple[dict, dict]:
            reported = self.peer_status(peer["pc_id"])
            if (not reported or int(reported.get("authority_protocol", 0))
                    != AUTHORITY_PROTOCOL):
                # Refresh stale discovery metadata before deciding that a peer
                # cannot participate in the current authority protocol.
                reported = self.network.call(peer["ip"], "status", {})
                if (reported.get("pc_id") != peer["pc_id"]
                        or int(reported.get("authority_protocol", 0))
                        != AUTHORITY_PROTOCOL):
                    raise RuntimeError(
                        "Peer authority protocol is unavailable or incompatible.")
                self.peer_statuses[peer["pc_id"]] = (time.time(), reported)
            pool_reply = self.network.call(
                peer["ip"], "push_snapshot", {"snapshot": snap})
            if (wallpaper and
                    pool_reply.get("wallpaper_id") != wallpaper[0]["id"]):
                wallpaper_reply = self.network.call(
                    peer["ip"], "wallpaper_sync", {
                        "metadata": wallpaper[0],
                        "content": base64.b64encode(wallpaper[1]).decode("ascii"),
                    })
                if wallpaper_reply.get("wallpaper_id") != wallpaper[0]["id"]:
                    raise RuntimeError("Peer did not save the locked wallpaper.")
            customer_reply = self.network.call(
                peer["ip"], "customer_sync", customer_payload)
            return pool_reply, customer_reply

        expected = {
            item["customer_id"]: int(item["revision"])
            for item in customer_payload["accounts"] + customer_payload["tombstones"]
        }
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as executor:
            futures = {executor.submit(push, peer): peer
                       for peer in peers}
            for future in as_completed(futures):
                peer = futures[future]
                try:
                    reply, customer_reply = future.result()
                    self.store.note_peer(peer["pc_id"], peer["ip"],
                                         int(reply["revision"]))
                    acknowledgments = customer_reply.get("acknowledgments", {})
                    if not isinstance(acknowledgments, dict) or any(
                            int(acknowledgments.get(customer_id, -1)) < revision
                            for customer_id, revision in expected.items()):
                        raise RuntimeError(
                            "Peer did not acknowledge durable customer revisions."
                        )
                    self.store.note_customer_sync(peer["pc_id"], acknowledgments)
                    if self.customer_sync_errors.pop(peer["pc_id"], None) is not None:
                        diagnostic_info(
                            "CUSTOMER_SYNC", "SYNC_RECOVERED",
                            pc=fingerprint(peer["pc_id"]),
                            revisions=len(acknowledgments),
                        )
                except Exception as error:
                    self.store.note_customer_sync(peer["pc_id"], {}, str(error))
                    message = str(error)[:240]
                    if self.customer_sync_errors.get(peer["pc_id"]) != message:
                        diagnostic_warning(
                            "CUSTOMER_SYNC", "SYNC_FAILED",
                            pc=fingerprint(peer["pc_id"]),
                            error_type=type(error).__name__, reason=message,
                        )
                    self.customer_sync_errors[peer["pc_id"]] = message
                    # The next sync pass retries both pool and customer state.

    def _poll_peer_statuses(self) -> None:
        snap = self.snapshot()
        if not snap:
            return
        peers = [item for item in self.store.peers()
                 if item["pc_id"] in snap["members"]
                 and item["pc_id"] != self.store.pc_id]
        if not peers:
            return
        successful: set[str] = set()
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as executor:
            futures = {executor.submit(self.network.call, item["ip"], "status", {}): item
                       for item in peers}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    status = future.result()
                    if status.get("pc_id") != item["pc_id"]:
                        continue
                    successful.add(item["pc_id"])
                    self.peer_failures[item["pc_id"]] = 0
                    self.store.note_peer(item["pc_id"], item["ip"])
                    with self.lock:
                        self.peer_statuses[item["pc_id"]] = (time.time(), status)
                    previous = self.connection_states.get(item["pc_id"])
                    if item["pc_id"] not in self.graceful_disconnects:
                        self.connection_states[item["pc_id"]] = True
                    if (previous is False
                            and item["pc_id"] not in self.graceful_disconnects
                            and self.is_admin()):
                        self._record_member_connection(item["pc_id"])
                        self._record_peer_notification(item["pc_id"], "online")
                        diagnostic_info("NETWORK", "CONNECTION_RESTORED",
                                        pc=fingerprint(item["pc_id"]),
                                        ip=item["ip"])
                    elif previous is None:
                        diagnostic_info("NETWORK", "CONNECTION_ESTABLISHED",
                                        pc=fingerprint(item["pc_id"]),
                                        ip=item["ip"])
                    active = snap["active_admin"]
                    if (item["pc_id"] == active.get("pc_id")
                            and status.get("admin")
                            and int(status.get("admin_term", -1))
                            == int(active.get("term", 0))
                            and int(status.get("authority_protocol", 0))
                            == AUTHORITY_PROTOCOL):
                        self._note_admin_claim(
                            item["pc_id"], int(active.get("term", 0)))
                    records = self.network.call(item["ip"], "session_records", {})["records"]
                    for record in records:
                        if self.store.merge_owner_record(record):
                            self.notify("changed")
                except Exception:
                    self.peer_failures[item["pc_id"]] = (
                        self.peer_failures.get(item["pc_id"], 0) + 1)
        for item in peers:
            pc_id = item["pc_id"]
            if (pc_id not in successful and self.peer_status(pc_id) is None
                    and self.peer_failures.get(pc_id, 0) >= 2):
                previous = self.connection_states.get(pc_id)
                graceful = pc_id in self.graceful_disconnects
                self.graceful_disconnects.discard(pc_id)
                self.connection_states[pc_id] = False
                if previous is True and not graceful and self.is_admin():
                    self._record_peer_notification(pc_id, "offline")
                    self._release_disconnected_customer(pc_id, "pc_offline")
                    diagnostic_warning("NETWORK", "CONNECTION_LOST",
                                       pc=fingerprint(pc_id), ip=item["ip"],
                                       failures=self.peer_failures.get(pc_id, 0))

    def _record_peer_notification(self, pc_id: str, event_type: str) -> None:
        """Snapshot the event-time name while retaining the immutable PC ID."""
        snap = self.snapshot()
        member = snap.get("members", {}).get(pc_id) if snap else None
        if not member:
            return
        self.store.add_notification(pc_id, member.get("name", "PC"), event_type)
        self.notify("changed")

    def _record_graceful_disconnect(self, pc_id: str) -> None:
        """Record one close and suppress the later heartbeat-offline duplicate."""
        if pc_id in self.graceful_disconnects:
            return
        self._record_peer_notification(pc_id, "closed")
        self._release_disconnected_customer(pc_id, "software_closed")
        self.graceful_disconnects.add(pc_id)
        self.connection_states[pc_id] = False

    def _release_disconnected_customer(self, pc_id: str, reason: str) -> bool:
        """Invalidate one disconnected PC's login without starting a cooldown."""
        with self.lock:
            if not self.is_admin():
                return False
            ownership = self.store.customer_ownership(pc_id=pc_id)
            if not ownership:
                return False
            retainer = self.store.retainer_for_login(
                ownership["customer_id"], pc_id, ownership["login_id"])
            if retainer:
                # Preserve the last durable checkpoint; detection latency is
                # offline time and must not be charged to the customer.
                self.retainer_clocks.pop(retainer["session_id"], None)
            self.store.force_release_customer(
                ownership["customer_id"], reason=reason)
        self.notify("changed")
        return True

    def _record_member_connection(self, pc_id: str,
                                  connected_at: float | None = None) -> None:
        """Persist only a confirmed offline-to-online transition."""
        connected_at = time.time() if connected_at is None else connected_at

        def record(state):
            member = state["members"].get(pc_id)
            if member is not None:
                member["last_connected_at"] = connected_at

        self.change(record)

    def _settle_admin_ownership(self) -> None:
        """A reachable higher claim wins; inactive PCs follow a live Admin."""
        snap = self.snapshot()
        if not snap:
            return
        own_rank = admin_rank(snap)
        own_admin = self.is_admin()
        peers = self.store.peers()
        live = [(peer, self.peer_status(peer["pc_id"])) for peer in peers
                if peer["pc_id"] in snap["members"]]
        live = [(peer, status) for peer, status in live
                if status and status.get("admin")
                and status.get("pc_id") == peer["pc_id"]
                and (not own_admin or
                     (int(status.get("admin_term", 0)), peer["pc_id"]) > own_rank)]
        live.sort(key=lambda item: (int(item[1].get("admin_term", 0)),
                                    item[0]["pc_id"]), reverse=True)
        for peer, _ in live:
            try:
                incoming = self.network.call(peer["ip"], "snapshot", {})["snapshot"]
                if self.accept_admin_snapshot(incoming, peer["pc_id"],
                                              follow_live_admin=True):
                    self.notify("changed")
                return
            except Exception:
                continue

    def customer_pc_states(self) -> dict[str, dict]:
        """Return Admin-authoritative registered presence keyed by immutable PC ID."""
        if not self.is_admin():
            return {}
        result: dict[str, dict] = {}
        now_mono = time.monotonic()
        for ownership in self.store.customer_ownerships():
            account = self.store.customer_account(ownership["customer_id"])
            if not account:
                continue
            retainer = self.store.retainer_for_login(
                ownership["customer_id"], ownership["pc_id"],
                ownership["login_id"])
            active = bool(retainer and retainer["state"] == "active")
            balance = account["balance_seconds"]
            if active:
                clock = self.retainer_clocks.get(retainer["session_id"])
                if clock:
                    balance = max(0, balance - int(max(0, now_mono - clock[0])))
            result[ownership["pc_id"]] = {
                "customerId": account["customer_id"],
                "customerName": account["name"],
                "retainerActive": active,
                "retainerBalanceSeconds": balance,
                "retainerSessionId": retainer["session_id"] if active else "",
            }
        return result

    def _reconcile_registered_customer(self) -> None:
        local = self.store.local_customer_session()
        available = self.is_admin() or self.confirmed_remote_admin_online()
        self.store.set_local("customer_admin_online", "1" if available else "0")
        if not local:
            return
        if not available:
            last_seen = float(local.get("last_admin_seen", 0))
            if time.time() - last_seen > PEER_ONLINE_SECONDS:
                self._force_local_customer_logout(
                    "Admin connection was lost. Registered access was locked "
                    "and returned to Guest mode."
                )
            return
        try:
            state = self._customer_admin_request("state", {
                "customer_id": local["customer_id"],
                "login_id": local["login_id"],
            })
        except PermissionError:
            # The Admin has definitively invalidated this login. Do not give a
            # reconnected stale PC another heartbeat window of registered access.
            self._force_local_customer_logout(
                "This registered login is no longer active. This PC was locked "
                "and returned to Guest mode."
            )
            return
        except Exception:
            if time.time() - float(local.get("last_admin_seen", 0)) > \
                    PEER_ONLINE_SECONDS:
                self._force_local_customer_logout(
                    "Registered login could not be verified. This PC was locked "
                    "and returned to Guest mode."
                )
            return
        updated = self._local_customer_from_state(
            state, local.get("feedback", ""))
        was_active = bool(local.get("retainer_active"))
        self.store.save_local_customer_session(updated)
        if was_active and not updated["retainer_active"]:
            updated["feedback"] = (
                "Retainer time exhausted. Ask staff to add time."
                if updated["balance_seconds"] == 0 else
                "Retainer play stopped; remaining time was saved."
            )
            self.store.save_local_customer_session(updated)
            self.store.set_local("customer_feedback", updated["feedback"])
            self._reconcile_local_access()

    def _tick_sessions(self) -> None:
        """Only this PC advances and finalizes its own session."""
        if self.local_shutdown_pending:
            return
        snap = self.snapshot()
        grace = snap["settings"]["grace_minutes"]
        now = time.time()
        session = snap["sessions"].get(self.store.pc_id)
        if not session:
            return
        current = sessions.phase(session, now, grace)
        if current == "expired":
            with self.lock:
                if self.local_shutdown_pending:
                    return
                session, history, grants = self._owner_payload()
                if session and sessions.phase(session, now, grace) == "expired":
                    sessions.enter_grace_if_due(session, now, grace)
                    ended = session["grace_started"] + grace * 60
                    history.append(sessions.finish_session(session, self.store.pc_id,
                        snap["members"][self.store.pc_id]["name"],
                        "grace_expired", ended))
                    self._save_owner_state(None, history, grants, ended)
        elif current == "grace" and session["grace_started"] is None:
            with self.lock:
                if self.local_shutdown_pending:
                    return
                session, history, grants = self._owner_payload()
                sessions.enter_grace_if_due(session, now, grace)
                self._save_owner_state(session, history, grants, now)

    def _reconcile_local_access(self) -> None:
        """A customer timer may lock locally even during an Admin outage."""
        if self.local_shutdown_pending:
            return
        if not self.console_available or self.console_handle is None:
            return
        snap = self.snapshot()
        if not snap:
            return
        own_session = snap["sessions"].get(self.store.pc_id)
        current = sessions.phase(own_session, grace_minutes=snap["settings"]["grace_minutes"])
        retainer_active = bool(
            (self.store.local_customer_session() or {}).get("retainer_active")
        )
        allowed = (current in ("buffer", "timed", "open", "paused")
                   or retainer_active or self.retainer_unlock_pending)
        if self.admin_ui_open:
            self.last_access_allowed = allowed
            return
        must_switch = (self.last_access_allowed is None
                       or self.last_access_allowed != allowed)
        if not allowed and active_desktop_name() == "Default":
            must_switch = True
        if must_switch:
            try:
                switch(self.default_handle if allowed else self.console_handle)
                self.last_access_allowed = allowed
            except Exception as error:
                self.notify("error", f"Could not change desktop: {error}")

    def _local_access_ready(self) -> bool:
        """Report whether this User PC applied its current access decision."""
        if not self.console_available or self.console_handle is None:
            return False
        snap = self.snapshot()
        if not snap or self.admin_ui_open:
            return False
        session = snap["sessions"].get(self.store.pc_id)
        ordinary_allowed = sessions.phase(
            session, grace_minutes=snap["settings"]["grace_minutes"]
        ) in ("buffer", "timed", "open", "paused")
        allowed = (ordinary_allowed or bool(
            (self.store.local_customer_session() or {}).get("retainer_active"))
            or self.retainer_unlock_pending)
        return self.last_access_allowed is allowed

    def _background_loop(self) -> None:
        last_renew = 0.0
        while not self.stop_event.is_set():
            try:
                if self.store.local("pending_pairing_revocation"):
                    if self._apply_pending_pairing_revocation():
                        self.stop_event.wait(SYNC_INTERVAL_SECONDS)
                        continue
                if not self.store.current_pool_id:
                    self.stop_event.wait(SYNC_INTERVAL_SECONDS)
                    continue
                snap = self.snapshot()
                if (snap and snap["active_admin"].get("pc_id") == self.store.pc_id
                        and self.local_claim_deadline <= time.monotonic()):
                    # Sleep/resume or a local clock adjustment must not strand
                    # the still-designated, cryptographically proven Admin.
                    self._resume_designated_admin()
                if self.is_admin() and time.monotonic() - last_renew >= 5:
                    self._renew_admin_lease()
                    last_renew = time.monotonic()
                self._discover_peers_once()
                if not self.store.current_pool_id:
                    continue
                self._poll_peer_statuses()
                self._reconcile_pending_commands()
                if not self.store.current_pool_id:
                    continue
                self.update_admin_connection_note()
                self._settle_admin_ownership()
                self._tick_sessions()
                self.checkpoint_local_session()
                self._checkpoint_active_retainers()
                self._reconcile_registered_customer()
                self._replicate_once()
                self._reconcile_local_access()
            except Exception as error:
                diagnostic_unexpected(
                    "RUNTIME", "BACKGROUND_ITERATION_FAILED", error,
                    pc=fingerprint(self.store.pc_id),
                    admin=self.is_admin())
                self.notify("error", str(error))
            self.stop_event.wait(SYNC_INTERVAL_SECONDS)
