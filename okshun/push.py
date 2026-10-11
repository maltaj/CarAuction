"""
Phone and browser notifications via the Web Push standard.

Implements the two pieces a push service needs, using only `cryptography`
and the standard library:
- VAPID (RFC 8292): a signed token proving the message comes from this server.
- Message encryption (RFC 8291, "aes128gcm"): only the buyer's browser can read it.

Keys: set OKSHUN_VAPID_PRIVATE and OKSHUN_VAPID_PUBLIC (base64url) to fix them,
otherwise a key pair is generated on first use and kept in the database.
Changing keys invalidates existing subscriptions, so keep them once in production.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Callable, Optional
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
DEFAULT_SUBJECT = "mailto:alerts@okshun.co.za"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------- keys ----------

def generate_keys() -> tuple[str, str]:
    """Returns (private, public) as base64url: raw 32-byte scalar and 65-byte uncompressed point."""
    priv = ec.generate_private_key(ec.SECP256R1())
    raw = priv.private_numbers().private_value.to_bytes(32, "big")
    return b64url(raw), b64url(_public_bytes(priv.public_key()))


def vapid_keys(conn: sqlite3.Connection) -> tuple[str, str]:
    env_priv, env_pub = os.environ.get("OKSHUN_VAPID_PRIVATE"), os.environ.get("OKSHUN_VAPID_PUBLIC")
    if env_priv and env_pub:
        return env_priv, env_pub
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'vapid'").fetchone()
    if row:
        d = json.loads(row["value"] if isinstance(row, sqlite3.Row) else row[0])
        return d["private"], d["public"]
    priv, pub = generate_keys()
    conn.execute("INSERT OR IGNORE INTO app_settings VALUES ('vapid', ?)", (json.dumps({"private": priv, "public": pub}),))
    conn.commit()
    return vapid_keys(conn)


def _load_private(priv_b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64url(priv_b64), "big"), ec.SECP256R1())


# ---------- VAPID (RFC 8292) ----------

def vapid_authorization(endpoint: str, priv_b64: str, pub_b64: str, subject: str = DEFAULT_SUBJECT,
                        expires_in: int = 12 * 3600) -> str:
    u = urlparse(endpoint)
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64url(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + expires_in,
                                "sub": subject}, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    der = _load_private(priv_b64).sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{sig}, k={pub_b64}"


# ---------- encryption (RFC 8291 / RFC 8188 aes128gcm) ----------

def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh_b64: str, auth_b64: str, *, _sender_key=None, _salt: Optional[bytes] = None) -> bytes:
    """Encrypt a payload for one browser subscription. Returns the full request body."""
    if len(payload) > RECORD_SIZE - 17 - 86:
        raise ValueError("Push payload too large")
    ua_public = unb64url(p256dh_b64)
    auth_secret = unb64url(auth_b64)
    sender = _sender_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(sender.public_key())
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = _salt or secrets.token_bytes(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)   # 0x02 = last record, no padding
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


# ---------- sending ----------

def send(subscription: dict, payload: dict, priv_b64: str, pub_b64: str, ttl: int = 3600,
         subject: str = DEFAULT_SUBJECT) -> int:
    """POST one notification to the browser's push service. Returns the HTTP status."""
    body = encrypt(json.dumps(payload).encode(), subscription["p256dh"], subscription["auth"])
    req = urllib.request.Request(subscription["endpoint"], data=body, method="POST", headers={
        "Authorization": vapid_authorization(subscription["endpoint"], priv_b64, pub_b64, subject),
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": "high",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


Sender = Callable[[dict, dict, str, str], int]


def push_to_user(conn: sqlite3.Connection, user_id: int, payload: dict, sender: Optional[Sender] = None) -> int:
    """Send to every device the buyer allowed. Removes subscriptions the push service says are gone."""
    priv, pub = vapid_keys(conn)
    subject = os.environ.get("OKSHUN_PUSH_SUBJECT", DEFAULT_SUBJECT)
    sender = sender or (lambda sub, data, k, pk: send(sub, data, k, pk, subject=subject))
    delivered = 0
    for sub in conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall():
        status = sender(dict(sub), payload, priv, pub)
        if status in (404, 410):
            conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
        elif 200 <= status < 300:
            delivered += 1
    conn.commit()
    return delivered
