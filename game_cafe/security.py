"""Password-protected Admin signing and encrypted join credentials."""

from __future__ import annotations

import hashlib
import json
import os


KEY_ITERATIONS = 600_000


def create_admin_signing_record(password: str) -> dict:
    """Share a public verifier; encrypt the private key under the password."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw,
                                          NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw,
                                                      PublicFormat.Raw)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    wrapping_key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                                       KEY_ITERATIONS)
    sealed = AESGCM(wrapping_key).encrypt(nonce, private_bytes, public_bytes)
    return {
        "public": public_bytes.hex(), "salt": salt.hex(),
        "nonce": nonce.hex(), "sealed_private": sealed.hex(),
        "iterations": KEY_ITERATIONS,
    }


def unlock_admin_signing_key(password: str, record: dict):
    """Decrypt the signing key only after the operator enters the password."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public = bytes.fromhex(record["public"])
    key = hashlib.pbkdf2_hmac("sha256", password.encode(),
                              bytes.fromhex(record["salt"]),
                              int(record["iterations"]))
    raw = AESGCM(key).decrypt(bytes.fromhex(record["nonce"]),
                               bytes.fromhex(record["sealed_private"]), public)
    private = Ed25519PrivateKey.from_private_bytes(raw)
    actual_public = private.public_key().public_bytes(Encoding.Raw,
                                                       PublicFormat.Raw)
    if actual_public != public:
        raise PermissionError("Admin signing key does not match pool identity.")
    return private


def sign_admin_proof(private_key, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return private_key.sign(canonical).hex()


def verify_admin_proof(public_hex: str, payload: dict, signature_hex: str) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    public.verify(bytes.fromhex(signature_hex), canonical)


def create_node_keypair() -> tuple[str, str]:
    """Return a private/public Ed25519 pair; only the public half is shared."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )

    private = Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(Encoding.Raw, PrivateFormat.Raw,
                                       NoEncryption()).hex()
    public_hex = private.public_key().public_bytes(Encoding.Raw,
                                                   PublicFormat.Raw).hex()
    return private_hex, public_hex


def node_public_from_private(private_hex: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign_node(private_hex: str, payload: dict) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return private.sign(canonical).hex()


def verify_node(public_hex: str, payload: dict, signature_hex: str) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    public.verify(bytes.fromhex(signature_hex), canonical)


def create_local_command_key():
    """Keep the controller half of the child-command key in memory only."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private, public.hex()


def _command_key(shared_secret: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"GameCafeConsole local password handoff v1").derive(shared_secret)


def seal_local_password(controller_public_hex: str, password: str) -> dict:
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey, X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    controller_public = X25519PublicKey.from_public_bytes(
        bytes.fromhex(controller_public_hex)
    )
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(Encoding.Raw,
                                                            PublicFormat.Raw)
    nonce = os.urandom(12)
    sealed = AESGCM(_command_key(ephemeral.exchange(controller_public))).encrypt(
        nonce, password.encode("utf-8"), ephemeral_public
    )
    return {"ephemeral": ephemeral_public.hex(), "nonce": nonce.hex(),
            "sealed": sealed.hex()}


def open_local_password(controller_private, package: dict) -> str:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    ephemeral_public = bytes.fromhex(package["ephemeral"])
    ephemeral = X25519PublicKey.from_public_bytes(ephemeral_public)
    plaintext = AESGCM(_command_key(controller_private.exchange(ephemeral))).decrypt(
        bytes.fromhex(package["nonce"]), bytes.fromhex(package["sealed"]),
        ephemeral_public
    )
    return plaintext.decode("utf-8")
