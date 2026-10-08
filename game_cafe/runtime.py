"""Pool authority, session writes, replication, and Client control."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import queue
import threading
import time
import uuid

from . import sessions
from .desktops import active_desktop_name, switch
from .network import (NodeNetwork, authenticated_call, create_pairing_key,
                      discover, open_welcome, pairing_code, pairing_secret,
                      plain_call, seal_welcome)
from .security import (create_admin_signing_record, sign_admin_proof,
                       sign_node, unlock_admin_signing_key, verify_admin_proof,
                       verify_node)
from .storage import Store, hash_password, verify_password


LEASE_SECONDS = 15
PEER_ONLINE_SECONDS = 9
SYNC_INTERVAL_SECONDS = 3


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
        self.admin_signing_key = None
        self.lease_challenges: dict[tuple[str, str], float] = {}
        self.handoff_until = 0.0
        self.run_id = uuid.uuid4().hex
        self.last_checkpoint = 0.0

    def start(self) -> None:
        self.network.start()
        try:
            # The bound LAN port excludes a second parent process on this PC.
            if self.store.current_pool_id:
                self.store.set_local(self._lease_key("until"), "0")
            self.recover_interrupted_session()
            self._migrate_legacy_owner_session()
        except Exception:
            self.network.stop()
            raise
        self.worker = threading.Thread(target=self._background_loop,
                                       name="cafe-sync", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.network.stop()
        if self.worker:
            self.worker.join()
            self.worker = None

    def release_admin(self) -> None:
        if self.store.current_pool_id and self.is_admin():
            self.store.set_local(self._lease_key("until"), "0")
        self.admin_signing_key = None

    def _owner_payload(self) -> tuple[dict | None, list[dict], list[dict]]:
        snap = self.snapshot()
        own = self.store.pc_id
        record = next((item["payload"] for item in self.store.owner_records()
                       if item["payload"]["pc_id"] == own), None)
        return (snap["sessions"].get(own),
                list(snap["history"].get(own, [])),
                list(record["grants"]) if record else [])

    def _save_owner_state(self, session: dict | None, history: list[dict],
                          grants: list[dict], now: float | None = None) -> dict:
        record = self.store.save_owner_record(
            session, history, grants, self.run_id,
            time.time() if now is None else now)
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

    def finish_local_session(self, reason: str = "customer") -> bool:
        with self.lock:
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
            self._reconcile_local_access()
            return True

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

    def is_admin(self) -> bool:
        snap = self.snapshot()
        if not snap:
            return False
        lease = snap["active_admin"]
        return (time.time() >= self.handoff_until
                and lease.get("pc_id") == self.store.pc_id
                and int(lease.get("term", 0)) == int(
                    self.store.local(self._lease_key("term")) or 0)
                and lease.get("expires_at", 0) > time.time()
                and self.store.local(self._lease_key("pc")) == self.store.pc_id
                and self.store.local(self._lease_key("run")) == self.run_id
                and float(self.store.local(self._lease_key("until")) or 0) > time.time())

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
        }

    def peer_status(self, pc_id: str) -> dict | None:
        with self.lock:
            item = self.peer_statuses.get(pc_id)
            if not item or time.time() - item[0] > PEER_ONLINE_SECONDS:
                return None
            return item[1]

    def verified_admin_online(self) -> bool:
        """Find a live Admin without depending on the last saved owner."""
        return self._find_live_admin() is not None

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
                        and latest["active_admin"].get("expires_at", 0) > time.time()):
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
                self.handoff_until = 0
            self.admin_ui_open = False
            self.notify("role_lost")
        return applied

    def update_admin_connection_note(self) -> None:
        snap = self.snapshot()
        if not snap:
            return
        admin_id = snap["active_admin"].get("pc_id")
        remote = self.peer_status(admin_id) if admin_id else None
        connected = self.is_admin() or bool(remote and remote.get("admin"))
        note = ("Admin connected" if connected else
                "Admin unavailable — current session continues locally; changes will sync."
                if snap["sessions"].get(self.store.pc_id) else
                "Admin unavailable — reconnecting. Eligible idle PCs may use Switch to Admin.")
        if self.store.local("admin_connection_note") != note:
            self.store.set_local("admin_connection_note", note)

    def _require_admin(self) -> None:
        if not self.is_admin():
            raise PermissionError("An active Admin lease is required.")

    def _hold_local_claim(self, term: int) -> None:
        """A local timeout stops a disconnected former Admin from writing."""
        if time.time() < self.handoff_until:
            raise RuntimeError("This PC just handed off Admin control; wait and retry.")
        self.store.set_local(self._lease_key("pc"), self.store.pc_id)
        self.store.set_local(self._lease_key("run"), self.run_id)
        self.store.set_local(self._lease_key("term"), str(term))
        self.store.set_local(self._lease_key("until"),
                             str(time.time() + LEASE_SECONDS))

    def can_become_admin(self) -> bool:
        snap = self.snapshot()
        return bool(snap and not snap["sessions"].get(self.store.pc_id))

    def take_admin(self, password: str) -> None:
        """Authenticate, then transfer from a live Admin or claim locally."""
        live_admin = self._find_live_admin()
        snap = self.snapshot()
        if not snap or not verify_password(password, snap["admin_password"]):
            raise PermissionError("Incorrect Admin password.")
        self.admin_signing_key = unlock_admin_signing_key(
            password, snap["admin_signing"]
        )
        if not self.can_become_admin():
            raise RuntimeError("End this PC's customer session before switching to Admin.")
        if live_admin:
            self._take_from_active_admin(live_admin[1])
        else:
            self.login_admin_with_unlocked_key()

    def _take_from_active_admin(self, ip: str) -> None:
        latest = self.network.call(ip, "snapshot", {})["snapshot"]
        self.accept_admin_snapshot(latest, latest["active_admin"]["pc_id"])
        snap = self.snapshot()
        previous = snap["active_admin"].get("pc_id")
        if previous == self.store.pc_id:
            return self.login_admin_with_unlocked_key()
        term = max(int(snap["active_admin"].get("term", 0)),
                   int(self.store.local(self._lease_key("term")) or 0)) + 1
        challenge = self.network.call(ip, "lease_challenge", {})["challenge"]
        proof = {"pool_id": snap["pool_id"], "candidate": self.store.pc_id,
                 "term": term, "challenge": challenge}
        reply = self.network.call(ip, "transfer_admin", {
            "proof": proof, "signature": sign_admin_proof(self.admin_signing_key, proof)
        })
        self.accept_admin_snapshot(reply["snapshot"], previous)
        self._hold_local_claim(term)
        self._activate_admin(term)

    def login_admin_with_unlocked_key(self) -> None:
        """Use a local claim when no reachable Admin responds."""
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
        term = max(int(snap["active_admin"].get("term", 0)),
                   int(self.store.local(self._lease_key("term")) or 0)) + 1
        self._hold_local_claim(term)
        self._activate_admin(term)

    def _activate_admin(self, term: int) -> None:
        if self.admin_signing_key is None:
            raise PermissionError("Admin password authorization is required.")
        if (self.store.local(self._lease_key("pc")) != self.store.pc_id
                or int(self.store.local(self._lease_key("term")) or 0) != term
                or float(self.store.local(self._lease_key("until")) or 0) <= time.time()):
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
        self.store.update(claim)
        self.admin_ui_open = True
        self.notify("admin_ready")
        self._replicate_once()

    def login_admin(self, password: str) -> None:
        """Password check followed by a first-available LAN Admin claim."""
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

    def _remote_session_action(self, action: str, pc_id: str,
                               details: dict) -> dict:
        with self.lock:
            self._require_admin()
            snap = self.snapshot()
            peer = next((p for p in self.store.peers() if p["pc_id"] == pc_id), None)
            if not peer or time.time() - peer["last_seen"] > PEER_ONLINE_SECONDS:
                raise RuntimeError("Target PC is offline; session control is unavailable.")
            pending_key = f"pending_session_command:{snap['pool_id']}:{pc_id}"
            saved = self.store.local(pending_key)
            if saved:
                signed_command = json.loads(saved)
                previous = signed_command["command"]
                same_action = previous["action"] == action and previous["details"] == details
                if not same_action or previous["term"] != snap["active_admin"]["term"]:
                    # Ask the owner before replacing an uncertain command.
                    records = self.network.call(peer["ip"], "session_records", {})["records"]
                    for item in records:
                        if item["payload"]["pc_id"] == pc_id:
                            self.store.merge_owner_record(item)
                    owner = next((item["payload"] for item in records
                                  if item["payload"]["pc_id"] == pc_id), None)
                    applied = bool(owner and any(
                        item["command"]["id"] == previous["id"]
                        for item in owner["grants"]))
                    if applied:
                        status = self.network.call(peer["ip"], "status", {})
                        if not status.get("access_ready"):
                            raise RuntimeError("Previous command saved, but desktop access is not confirmed.")
                        self.store.delete_local(pending_key)
                        if same_action:
                            return self.snapshot()
                        saved = None
                    elif previous["term"] != snap["active_admin"]["term"]:
                        self.store.delete_local(pending_key)
                        saved = None
                if saved and not same_action:
                    raise RuntimeError(
                        "A previous session command has an uncertain result. "
                        "Retry that command before sending a different one.")
            if not saved:
                session = snap["sessions"].get(pc_id)
                command = {
                    "id": uuid.uuid4().hex, "pool_id": snap["pool_id"],
                    "admin_id": self.store.pc_id, "target": pc_id,
                    "term": snap["active_admin"]["term"], "action": action,
                    "session_id": uuid.uuid4().hex if action == "start" else
                                  (session["id"] if session else None),
                    "details": details,
                }
                signed_command = {
                    "command": command,
                    "signature": sign_node(self.store.node_private_key(), command),
                    "admin_proof": sign_admin_proof(self.admin_signing_key, command),
                }
                self.store.set_local(pending_key, json.dumps(signed_command))
            # The target must see this Admin claim before checking the command.
            self.network.call(peer["ip"], "push_snapshot", {"snapshot": snap})
            reply = self.network.call(peer["ip"], "session_command", signed_command)
            if reply.get("target") != pc_id or not reply.get("saved"):
                raise RuntimeError("Target did not confirm the local session write.")
            if not reply.get("access_ready"):
                raise RuntimeError(
                    "Target saved the session command but could not confirm desktop access. "
                    "Check the User Console and retry the same action.")
            if reply.get("record"):
                self.store.merge_owner_record(reply["record"])
            self.store.delete_local(pending_key)
            if action in ("start", "end"):
                self.change(lambda state: state["unlock_requests"].pop(pc_id, None))
            self.notify("changed")
            return self.snapshot()

    def remote_exit(self, pc_id: str) -> dict:
        self._require_admin()
        snap = self.snapshot()
        if pc_id == self.store.pc_id or pc_id not in snap["members"]:
            raise ValueError("Choose a registered User PC.")
        status = self.peer_status(pc_id)
        peer = next((item for item in self.store.peers()
                     if item["pc_id"] == pc_id), None)
        if not status or not peer:
            raise RuntimeError("Target PC is offline; Exit Software is unavailable.")
        try:
            self.network.call(peer["ip"], "push_snapshot", {"snapshot": snap})
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
        # The process is still sending this reply; offline status follows disconnect.
        return reply

    def submit_customer_action(self, action: str, **details) -> None:
        """Ask the active Admin to perform a customer-originated write."""
        if action in ("end", "rename"):
            if action == "end":
                self.finish_local_session("customer")
            else:
                name = str(details["name"]).strip()
                if not 1 <= len(name) <= 40:
                    raise ValueError("Player name must be 1 to 40 characters.")
                with self.lock:
                    session, history, grants = self._owner_payload()
                    if not session:
                        raise ValueError("No active session.")
                    session["player"] = name
                    self._save_owner_state(session, history, grants)
            return
        if self.is_admin():
            self._customer_action(action, details, self.store.pc_id)
            return
        ip = self.active_admin_ip()
        if not ip:
            raise RuntimeError("Admin is offline. Try again when it reconnects.")
        self.network.call(ip, "customer_action",
                          {"action": action, "details": details})

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
            snap = self.snapshot()
            command = data["command"]
            claim = snap["active_admin"]
            if (sender != claim["pc_id"] or sender != command["admin_id"]
                    or claim["expires_at"] <= time.time()
                    or command["term"] != claim["term"]
                    or command["pool_id"] != snap["pool_id"]
                    or command["target"] != self.store.pc_id):
                raise PermissionError("Only the current Admin can control this session.")
            verify_node(snap["members"][sender]["public_key"], command,
                        data["signature"])
            verify_admin_proof(snap["admin_signing"]["public"], command,
                               data["admin_proof"])
            session, history, grants = self._owner_payload()
            action, details = command["action"], command["details"]
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
                session = sessions.start_session(details["kind"],
                    int(details.get("paid_minutes", 0)),
                    int(details.get("buffer_minutes", 0)))
                session["id"] = command["session_id"]
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
                    session = None
                else:
                    raise ValueError("Unsupported session command.")
            grants.append(data)
            record = self._save_owner_state(session, history, grants)
            self._reconcile_local_access()
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
            if (len(request_id) != 32 or len(pc_id) != 32
                    or len(public_key) != 64 or len(pairing_public) != 64
                    or not name.strip()):
                raise ValueError("Invalid join request.")
            bytes.fromhex(public_key)
            bytes.fromhex(pairing_public)
            self.store.record_join(request_id, pc_id, name, ip, public_key,
                                   pairing_public)
            self.notify("join_request")
            return {"state": "pending"}
        if operation == "join_poll":
            request_id = str(data.get("request_id", ""))
            decision = self.store.join_decision(request_id)
            if not decision:
                raise ValueError("Unknown join request.")
            return decision
        raise ValueError("Unsupported public request.")

    def prepare_join_pairing(self, request_id: str) -> None:
        """Publish an ephemeral public key so the joining PC can show its code."""
        self._require_admin()
        pending = next((item for item in self.store.pending_joins()
                        if item["request_id"] == request_id), None)
        if not pending:
            raise ValueError("Join request expired or is no longer pending.")
        private, admin_public = create_pairing_key()
        shared = pairing_secret(private, pending["pairing_public"])
        self.store.set_join_pairing(request_id, admin_public, shared.hex())

    def approve_join(self, request_id: str, pc_id: str,
                     verification_code: str, name: str) -> None:
        self._require_admin()
        pending = next((item for item in self.store.pending_joins()
                        if item["request_id"] == request_id and item["pc_id"] == pc_id), None)
        if not pending:
            raise ValueError("Join request is no longer pending.")
        if not pending["pairing_secret"]:
            raise RuntimeError("Pairing has not started; choose Accept again.")
        shared = bytes.fromhex(pending["pairing_secret"])
        expected = pairing_code(shared, request_id)
        shared = bytes.fromhex(self.store.verify_join_code(
            request_id, pc_id, verification_code.strip().upper(), expected
        ))
        name = name.strip()
        if not name or len(name) > 40:
            raise ValueError("PC name must be 1 to 40 characters.")
        updated = self.change(lambda state: state["members"].update(
            {pc_id: {"name": name, "public_key": pending["public_key"]}}
        ))
        welcome = {"snapshot": updated, "secret": self.store.secret(),
                   "pc_id": pc_id}
        self.store.decide_join(request_id, "accepted",
                               seal_welcome(shared, request_id, welcome))
        self.store.remember_address(pc_id, pending["ip"])

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
        if operation == "session_record":
            record = data["record"]
            if record["payload"]["pc_id"] != sender:
                raise PermissionError("Only an owner can publish its session record.")
            return {"applied": self.store.merge_owner_record(record)}
        if operation == "session_command":
            return self._apply_session_command(data, sender)
        if operation == "remote_exit":
            claim = snap["active_admin"]
            if (sender != claim.get("pc_id") or
                    claim.get("expires_at", 0) <= time.time() or
                    int(data.get("term", -1)) != claim.get("term") or
                    self.store.pc_id == sender):
                raise PermissionError("Only the active Admin can exit a User PC.")
            self.finish_local_session("close_software")
            self.notify("remote_exit")
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
        if operation == "transfer_admin":
            with self.lock:
                self._require_admin()
                snap = self.snapshot()
                proof = data.get("proof", {})
                if (proof.get("candidate") != sender
                        or proof.get("pool_id") != snap["pool_id"]
                        or int(proof.get("term", 0)) <= snap["active_admin"]["term"]
                        or snap["sessions"].get(sender)):
                    raise PermissionError("PC is not eligible for Admin transfer.")
                challenge = proof.get("challenge")
                expiry = self.lease_challenges.pop((sender, challenge), 0)
                if expiry <= time.time():
                    raise PermissionError("Admin transfer challenge expired.")
                verify_admin_proof(snap["admin_signing"]["public"], proof,
                                   data.get("signature", ""))
                # Stop Admin writes before the new PC claims the role.
                self.handoff_until = time.time() + 30
                self.store.set_local(self._lease_key("until"), "0")
                self.admin_ui_open = False
                self.notify("role_lost")
                return {"snapshot": snap}
        if operation == "push_snapshot":
            incoming = data.get("snapshot")
            if not isinstance(incoming, dict) or incoming.get("pool_id") != snap["pool_id"]:
                raise ValueError("Invalid pool snapshot.")
            lease = incoming.get("active_admin", {})
            if lease.get("expires_at", 0) < time.time():
                raise PermissionError("Admin claim has expired.")
            applied = self.accept_admin_snapshot(incoming, sender)
            self.notify("changed")
            self._reconcile_local_access()
            return {"revision": self.snapshot()["revision"], "applied": applied,
                    "admin_rank": admin_rank(self.snapshot()),
                    "access_ready": self._local_access_ready()}
        if operation == "customer_action":
            self._customer_action(data["action"], data.get("details", {}), sender)
            return {"revision": self.snapshot()["revision"]}
        raise ValueError("Unsupported member request.")

    def request_join(self, ip: str, pool_id: str, requested_name: str):
        """Send a join request and retain the private half only on this PC."""
        private, public = create_pairing_key()
        request_id = uuid.uuid4().hex
        plain_call(ip, "join_request", {"pool_id": pool_id,
                                        "pc_id": self.store.pc_id,
                                        "request_id": request_id,
                                        "name": requested_name,
                                        "public_key": self.store.node_public_key(),
                                        "pairing_public": public})
        return request_id, private

    def finish_join(self, ip: str, request_id: str, pairing_private,
                    old_pool_id: str | None = None) -> tuple[str, str | None]:
        reply = plain_call(ip, "join_poll", {"request_id": request_id})
        if reply["state"] == "pending":
            public = reply.get("pairing_admin_public")
            if public:
                shared = pairing_secret(pairing_private, public)
                return "verify", pairing_code(shared, request_id)
            return "pending", None
        if reply["state"] in ("rejected", "expired"):
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
                    and item["pc_id"] in snap["members"]
                    and item["pc_id"] != self.store.pc_id):
                self.store.remember_address(item["pc_id"], item["ip"])
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
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as executor:
            futures = {executor.submit(self.network.call, peer["ip"],
                                       "push_snapshot", {"snapshot": snap}): peer
                       for peer in peers}
            for future in as_completed(futures):
                peer = futures[future]
                try:
                    reply = future.result()
                    self.store.note_peer(peer["pc_id"], peer["ip"],
                                         int(reply["revision"]))
                except Exception:
                    pass  # The next sync pass retries a full snapshot.

    def _poll_peer_statuses(self) -> None:
        snap = self.snapshot()
        if not snap:
            return
        peers = [item for item in self.store.peers()
                 if item["pc_id"] in snap["members"]
                 and item["pc_id"] != self.store.pc_id]
        if not peers:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as executor:
            futures = {executor.submit(self.network.call, item["ip"], "status", {}): item
                       for item in peers}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    status = future.result()
                    if status.get("pc_id") != item["pc_id"]:
                        continue
                    self.store.note_peer(item["pc_id"], item["ip"])
                    with self.lock:
                        self.peer_statuses[item["pc_id"]] = (time.time(), status)
                    records = self.network.call(item["ip"], "session_records", {})["records"]
                    for record in records:
                        if self.store.merge_owner_record(record):
                            self.notify("changed")
                except Exception:
                    pass

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

    def _tick_sessions(self) -> None:
        """Only this PC advances and finalizes its own session."""
        snap = self.snapshot()
        grace = snap["settings"]["grace_minutes"]
        now = time.time()
        session = snap["sessions"].get(self.store.pc_id)
        if not session:
            return
        current = sessions.phase(session, now, grace)
        if current == "expired":
            with self.lock:
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
                session, history, grants = self._owner_payload()
                sessions.enter_grace_if_due(session, now, grace)
                self._save_owner_state(session, history, grants, now)

    def _reconcile_local_access(self) -> None:
        """A customer timer may lock locally even during an Admin outage."""
        if not self.console_available or self.console_handle is None:
            return
        snap = self.snapshot()
        if not snap:
            return
        own_session = snap["sessions"].get(self.store.pc_id)
        current = sessions.phase(own_session, grace_minutes=snap["settings"]["grace_minutes"])
        allowed = current in ("buffer", "timed", "open", "paused")
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
        allowed = sessions.phase(
            session, grace_minutes=snap["settings"]["grace_minutes"]
        ) in ("buffer", "timed", "open", "paused")
        return self.last_access_allowed is allowed

    def _background_loop(self) -> None:
        last_renew = 0.0
        while not self.stop_event.is_set():
            try:
                if self.is_admin() and time.monotonic() - last_renew >= 5:
                    self._renew_admin_lease()
                    last_renew = time.monotonic()
                self._discover_peers_once()
                self._poll_peer_statuses()
                self.update_admin_connection_note()
                self._settle_admin_ownership()
                self._tick_sessions()
                self.checkpoint_local_session()
                self._replicate_once()
                self._reconcile_local_access()
            except Exception as error:
                self.notify("error", str(error))
            self.stop_event.wait(SYNC_INTERVAL_SECONDS)
