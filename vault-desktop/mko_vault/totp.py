"""Einmalcodes (TOTP, RFC 6238) selbst berechnen.

Nur als Rückfalllösung: `bw serve` liefert den Code über /object/totp/{id}, verlangt dafür
aber bei manchen Servern ein Premium-Konto. Vaultwarden gibt allen Benutzern Premium, ein
selbst berechneter Code ist dann gar nicht nötig.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, unquote, urlparse

_ALGOS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}
_STEAM = "23456789BCDFGHJKMNPQRTVWXY"


def _b32(secret: str) -> bytes:
    s = secret.replace(" ", "").replace("-", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code(totp: str, now: float | None = None) -> str:
    """Code zu einem Geheimnis („JBSWY3DP…“, otpauth://…-Adresse oder steam://…)."""
    if not totp:
        raise ValueError("kein TOTP hinterlegt")
    digits, period, algo, steam = 6, 30, "SHA1", False
    secret = totp.strip()
    if secret.lower().startswith("otpauth://"):
        u = urlparse(secret)
        q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
        secret = unquote(q.get("secret", ""))
        digits = int(q.get("digits", 6))
        period = int(q.get("period", 30))
        algo = q.get("algorithm", "SHA1").upper()
    elif secret.lower().startswith("steam://"):
        secret, digits, steam = secret[8:], 5, True
    key = _b32(secret)
    counter = int((time.time() if now is None else now) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), _ALGOS.get(algo, hashlib.sha1)).digest()
    off = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    if steam:
        out = ""
        for _ in range(digits):
            value, idx = divmod(value, len(_STEAM))
            out += _STEAM[idx]
        return out
    return str(value % 10 ** digits).zfill(digits)
