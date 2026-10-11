"""LAN discovery, authenticated JSON requests, and protected onboarding."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import threading
import time
import uuid

from .storage import Store
from .lifecycle import lifecycle_event
from .security import (sign_node, verify_node,
                       verify_pairing_revocation)


PROTOCOL = "GAME_CAFE/1"
DISCOVERY_PORT = 48120
COMMAND_PORT = 48121
MAX_MESSAGE = 1_000_000
TIMEOUT = 2.5
REPLAY_WINDOW = 30


class PairingRevokedError(PermissionError):
    """A valid Admin certificate revoked this PC's saved authorization."""

    def __init__(self, notice: dict):
        super().__init__("This computer's cafe pairing was removed by the Admin.")
        self.notice = notice


def compact(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def send_message(connection: socket.socket, message: dict) -> None:
    payload = compact(message)
    if len(payload) > MAX_MESSAGE:
        raise ValueError("Message exceeds size limit.")
    connection.sendall(payload + b"\n")


def read_message(connection: socket.socket) -> dict:
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(8192)
        if not chunk:
            raise ConnectionError("Connection closed before message completed.")
        data.extend(chunk)
        if len(data) > MAX_MESSAGE:
            raise ValueError("Message exceeds size limit.")
    message = json.loads(bytes(data).split(b"\n", 1)[0])
    if not isinstance(message, dict):
        raise ValueError("Expected a JSON object.")
    return message


def signed(secret_hex: str, sender: str, pool_id: str,
           operation: str, data: dict,
           private_key: str | None = None,
           authorization_id: str | None = None) -> dict:
    message = {
        "protocol": PROTOCOL, "pool_id": pool_id, "sender": sender,
        "operation": operation, "data": data, "timestamp": time.time(),
        "nonce": uuid.uuid4().hex,
    }
    if authorization_id:
        message["authorization_id"] = authorization_id
    if private_key:
        message["signature"] = sign_node(private_key, message)
    message["mac"] = hmac.new(bytes.fromhex(secret_hex), compact(message),
                               hashlib.sha256).hexdigest()
    return message


def verify_signed(message: dict, secret_hex: str, members: dict,
                  seen_nonces: dict[str, float] | None = None) -> None:
    if message.get("protocol") != PROTOCOL:
        raise ValueError("Unsupported protocol.")
    if message.get("sender") not in members:
        raise PermissionError("Sender is not registered in this pool.")
    member = members[message["sender"]]
    expected_authorization = member.get("authorization_id")
    if (expected_authorization is not None
            and message.get("authorization_id") != expected_authorization):
        raise PermissionError("Pairing authorization is no longer valid.")
    timestamp = float(message.get("timestamp", 0))
    if abs(time.time() - timestamp) > REPLAY_WINDOW:
        raise PermissionError("Request expired or system clocks differ.")
    nonce = message.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 16:
        raise PermissionError("Invalid request nonce.")
    unsigned = {key: value for key, value in message.items() if key != "mac"}
    expected = hmac.new(bytes.fromhex(secret_hex), compact(unsigned),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(message.get("mac", ""))):
        raise PermissionError("Invalid message authentication code.")
    public_key = member.get("public_key")
    if public_key:
        if not isinstance(message.get("signature"), str):
            raise PermissionError("Missing PC identity signature.")
        signed_body = {key: value for key, value in unsigned.items()
                       if key != "signature"}
        try:
            verify_node(public_key, signed_body, message["signature"])
        except Exception as error:
            raise PermissionError("Invalid PC identity signature.") from error
    if seen_nonces is not None:
        now = time.time()
        for key, seen_at in list(seen_nonces.items()):
            if now - seen_at > REPLAY_WINDOW:
                del seen_nonces[key]
        if nonce in seen_nonces:
            raise PermissionError("Replayed request.")
        seen_nonces[nonce] = now


def create_pairing_key():
    """Create a fresh X25519 key for one join request."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, public.hex()


def pairing_secret(private, peer_public_hex: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey

    public = bytes.fromhex(peer_public_hex)
    if len(public) != 32:
        raise ValueError("Invalid pairing public key.")
    return private.exchange(X25519PublicKey.from_public_bytes(public))


def _pairing_bytes(shared: bytes, request_id: str, purpose: bytes,
                   length: int) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    request = bytes.fromhex(request_id)
    if len(shared) != 32 or len(request) != 16:
        raise ValueError("Invalid pairing secret or request ID.")
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=request,
                info=b"GameCafeConsole pairing v2 " + purpose).derive(shared)


def seal_welcome(shared: bytes, request_id: str, welcome: dict) -> str:
    """Encrypt pool credentials with the full X25519-derived key, not the short code."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    key = _pairing_bytes(shared, request_id, b"welcome encryption", 32)
    ciphertext = AESGCM(key).encrypt(nonce, compact(welcome),
                                       bytes.fromhex(request_id))
    return (nonce + ciphertext).hex()


def open_welcome(shared: bytes, request_id: str, sealed: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    payload = bytes.fromhex(sealed)
    if len(payload) < 29:
        raise ValueError("Invalid welcome package.")
    key = _pairing_bytes(shared, request_id, b"welcome encryption", 32)
    result = json.loads(AESGCM(key).decrypt(payload[:12], payload[12:],
                                           bytes.fromhex(request_id)))
    if not isinstance(result, dict):
        raise ValueError("Invalid welcome package.")
    return result


def plain_call(ip: str, operation: str, data: dict) -> dict:
    """Only discovery, join request and join polling use unauthenticated calls."""
    with socket.create_connection((ip, COMMAND_PORT), TIMEOUT) as connection:
        connection.settimeout(TIMEOUT)
        send_message(connection, {"protocol": PROTOCOL,
                                  "operation": operation, "data": data})
        response = read_message(connection)
    if not response.get("ok"):
        raise RuntimeError(str(response.get("error", "Request failed.")))
    return response.get("data", {})


def authenticated_call(ip: str, sender: str, snapshot: dict,
                       secret: str, operation: str, data: dict,
                       private_key: str | None = None) -> dict:
    """Use a candidate pool during join, before switching local membership."""
    request = signed(secret, sender, snapshot["pool_id"], operation, data,
                     private_key, snapshot["members"].get(
                         sender, {}).get("authorization_id"))
    with socket.create_connection((ip, COMMAND_PORT), TIMEOUT) as connection:
        connection.settimeout(TIMEOUT)
        send_message(connection, request)
        response = read_message(connection)
    revocation = response.get("revocation")
    if revocation is not None:
        member = snapshot["members"].get(sender, {})
        verify_pairing_revocation(
            snapshot, revocation, sender, member.get("public_key"),
            member.get("authorization_id"))
        raise PairingRevokedError(revocation)
    verify_signed(response, secret, snapshot["members"])
    body = response.get("data", {})
    if response.get("operation") != "response" or body.get("for") != request["nonce"]:
        raise PermissionError("Response did not match request.")
    if not body.get("ok"):
        raise RuntimeError(str(body.get("error", "Request failed.")))
    return body.get("result", {})


def discover(timeout: float = 1.2) -> list[dict]:
    """Broadcast on the local subnet and collect pool advertisements."""
    results: dict[tuple[str, str], dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        sock.sendto(compact({"protocol": PROTOCOL, "operation": "discover"}),
                    ("255.255.255.255", DISCOVERY_PORT))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                payload, address = sock.recvfrom(4096)
                item = json.loads(payload)
                if not isinstance(item, dict):
                    continue
                if item.get("protocol") != PROTOCOL or "pool_id" not in item:
                    continue
                item["ip"] = address[0]
                results[(item["pool_id"], item.get("pc_id", ""))] = item
            except (socket.timeout, OSError, ValueError, TypeError):
                continue
    return list(results.values())


class NodeNetwork:
    """One server per PC; all accepted pool mutations run through its runtime."""

    def __init__(self, store: Store, runtime):
        self.store = store
        self.runtime = runtime
        self.stop_event = threading.Event()
        self.tcp: socket.socket | None = None
        self.udp: socket.socket | None = None
        self.threads: list[threading.Thread] = []
        self.seen_nonces: dict[str, float] = {}
        self.nonce_lock = threading.Lock()

    def start(self) -> None:
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tcp.bind(("", COMMAND_PORT))
            tcp.listen(16)
            tcp.settimeout(0.5)
            udp.bind(("", DISCOVERY_PORT))
            udp.settimeout(0.5)
        except Exception:
            tcp.close()
            udp.close()
            raise
        self.tcp, self.udp = tcp, udp
        self.threads = [
            threading.Thread(target=self._tcp_loop, daemon=True,
                             name="cafe-tcp"),
            threading.Thread(target=self._udp_loop, daemon=True,
                             name="cafe-discovery"),
        ]
        for thread in self.threads:
            thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        lifecycle_event("network_sockets_closing")
        for sock in (self.tcp, self.udp):
            if sock:
                sock.close()
        for thread in self.threads:
            lifecycle_event("network_listener_join_started",
                            worker=thread.name, worker_alive=thread.is_alive())
            thread.join(timeout=1.0)
            lifecycle_event("network_listener_join_completed",
                            worker=thread.name, worker_alive=thread.is_alive())
        self.tcp = self.udp = None

    def call(self, ip: str, operation: str, data: dict) -> dict:
        """Make a signed member request and validate the signed response."""
        snapshot = self.store.snapshot()
        secret = self.store.secret()
        if not snapshot or not secret:
            raise RuntimeError("This PC has not joined a cafe.")
        try:
            return authenticated_call(
                ip, self.store.pc_id, snapshot, secret, operation, data,
                self.store.node_private_key())
        except PairingRevokedError as error:
            self.runtime.receive_pairing_revocation(error.notice)
            raise

    def _udp_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                payload, address = self.udp.recvfrom(4096)
                request = json.loads(payload)
                if not isinstance(request, dict):
                    continue
                if request.get("protocol") != PROTOCOL or request.get("operation") != "discover":
                    continue
                snapshot = self.store.snapshot()
                if not snapshot:
                    continue
                advertisement = {
                    "protocol": PROTOCOL, "pool_id": snapshot["pool_id"],
                    "cafe_name": snapshot["cafe_name"],
                    "pc_id": self.store.pc_id,
                    "pc_name": snapshot["members"].get(self.store.pc_id, {}).get("name", "PC"),
                    "revision": snapshot["revision"],
                    "active_admin": snapshot["active_admin"].get("pc_id"),
                }
                self.udp.sendto(compact(advertisement), address)
            except (socket.timeout, OSError, ValueError, TypeError):
                continue

    def _tcp_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                connection, address = self.tcp.accept()
                threading.Thread(target=self._serve_one, args=(connection, address[0]),
                                 daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                return

    def _serve_one(self, connection: socket.socket, ip: str) -> None:
        with connection:
            connection.settimeout(TIMEOUT)
            try:
                request = read_message(connection)
                operation = request.get("operation")
                if request.get("protocol") != PROTOCOL:
                    raise ValueError("Unsupported protocol.")
                if operation in ("join_request", "join_poll", "join_cancel"):
                    result = self.runtime.handle_plain(operation, request.get("data", {}), ip)
                    send_message(connection, {"ok": True, "data": result})
                    return
                snapshot = self.store.snapshot()
                secret = self.store.secret()
                if not snapshot or not secret or request.get("pool_id") != snapshot["pool_id"]:
                    raise PermissionError("Unknown cafe pool.")
                try:
                    with self.nonce_lock:
                        verify_signed(
                            request, secret, snapshot["members"],
                            self.seen_nonces)
                except Exception as member_error:
                    notice = snapshot.get("revocations", {}).get(
                        str(request.get("sender", "")))
                    if notice:
                        certificate = verify_pairing_revocation(
                            snapshot, notice,
                            str(request.get("sender", "")))
                        revoked_member = {
                            "public_key": certificate["public_key"],
                            "authorization_id": certificate[
                                "authorization_id"],
                        }
                        try:
                            with self.nonce_lock:
                                verify_signed(
                                    request, secret,
                                    {certificate["pc_id"]: revoked_member},
                                    self.seen_nonces)
                        except Exception:
                            raise member_error
                        send_message(connection, {
                            "ok": False,
                            "error": "Pairing authorization was revoked.",
                            "revocation": notice,
                        })
                        return
                    raise
                self.store.note_peer(request["sender"], ip)
                try:
                    result = self.runtime.handle_member(
                        operation, request.get("data", {}), request["sender"], ip
                    )
                    body = {"for": request["nonce"], "ok": True, "result": result}
                except Exception as error:
                    body = {"for": request["nonce"], "ok": False,
                            "error": str(error)}
                response = signed(secret, self.store.pc_id, snapshot["pool_id"],
                                  "response", body,
                                  self.store.node_private_key(),
                                  snapshot["members"].get(
                                      self.store.pc_id, {}).get(
                                          "authorization_id"))
                send_message(connection, response)
                if operation == "remote_exit" and body["ok"] and result.get("accepted"):
                    # Exit only after the acknowledgement has been sent.
                    self.runtime.notify("remote_exit")
                if (operation == "pairing_revoked" and body["ok"]
                        and result.get("accepted")):
                    self.runtime.receive_pairing_revocation(
                        request.get("data", {}).get("revocation"))
            except Exception as error:
                try:
                    send_message(connection, {"ok": False, "error": str(error)})
                except OSError:
                    pass
