"""
Shared-secret HMAC authentication for the localhost-only internal routes.

The internal hook / ask endpoints (``handlers/internal.py``) are reached by
the ohrs subprocess over loopback. The loopback-host check is necessary but
not sufficient defence-in-depth (API-5): with ``host=0.0.0.0`` plus a
reverse-proxy/sidecar the client host can become a proxy address. This module
adds an HMAC-SHA256 header the platform mints and verifies, so authenticity no
longer rests solely on network topology.

Two message shapes are supported, both keyed on the same shared secret
(``settings.internal_hook_secret``):

* **Static (ohrs HTTP hooks)** — ohrs serialises the hook body itself and only
  supports *static* per-hook headers, so the HMAC is computed over the
  ``message_id`` alone (which is fixed for the hook's lifetime and carried in
  the URL path). This proves the caller knew the secret and bound it to this
  message. Header: :data:`HOOK_SIGNATURE_HEADER`.
* **Timestamped (``capelle-ask`` CLI)** — the CLI builds the request itself and
  can sign ``"{timestamp}.{body}"`` freshly per call, with a replay window.
  Headers: :data:`HOOK_TIMESTAMP_HEADER` + :data:`HOOK_SIGNATURE_HEADER`.

All comparisons use :func:`hmac.compare_digest` (constant time).
"""

from __future__ import annotations

import hashlib
import hmac
import time

# Header carrying the hex-encoded HMAC-SHA256 signature.
HOOK_SIGNATURE_HEADER: str = "X-Capelle-Signature"
# Header carrying the unix-seconds timestamp for the timestamped variant.
HOOK_TIMESTAMP_HEADER: str = "X-Capelle-Timestamp"

# Max age (seconds) a timestamped signature is accepted for — bounds replay.
_MAX_SIGNATURE_AGE_SECONDS: int = 300


def sign_message_id(secret: str, message_id: str) -> str:
    """
    Mint the static signature for an ohrs HTTP hook bound to ``message_id``.

    Args:
        secret: The shared internal-hook secret.
        message_id: The assistant message id the hook targets.

    Returns:
        Hex-encoded HMAC-SHA256 of ``message_id``.
    """
    return hmac.new(
        secret.encode("utf-8"), message_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def sign_timestamped(secret: str, timestamp: str, body: bytes) -> str:
    """
    Mint a timestamped signature over ``"{timestamp}.{body}"``.

    Args:
        secret: The shared internal-hook secret.
        timestamp: Unix-seconds timestamp as a string.
        body: The raw request body bytes.

    Returns:
        Hex-encoded HMAC-SHA256 of the timestamp-prefixed body.
    """
    payload = timestamp.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_hook_signature(
    secret: str, message_id: str, provided_signature: str | None
) -> bool:
    """
    Verify the static hook signature for ``message_id``.

    Returns ``True`` when ``secret`` is empty (HMAC disabled — caller falls
    back to the loopback guard only) or the provided signature matches.
    """
    if not secret:
        return True
    if not provided_signature:
        return False
    expected = sign_message_id(secret, message_id)
    return hmac.compare_digest(expected, provided_signature)


def verify_timestamped_signature(
    secret: str,
    timestamp: str | None,
    body: bytes,
    provided_signature: str | None,
    *,
    now: float | None = None,
) -> bool:
    """
    Verify a timestamped signature, rejecting stale (replayable) timestamps.

    Returns ``True`` when ``secret`` is empty (HMAC disabled). Otherwise both
    the timestamp and signature must be present, the timestamp must be within
    :data:`_MAX_SIGNATURE_AGE_SECONDS` of ``now``, and the signature must match.
    """
    if not secret:
        return True
    if not timestamp or not provided_signature:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = now if now is not None else time.time()
    if abs(current - ts) > _MAX_SIGNATURE_AGE_SECONDS:
        return False
    expected = sign_timestamped(secret, timestamp, body)
    return hmac.compare_digest(expected, provided_signature)
