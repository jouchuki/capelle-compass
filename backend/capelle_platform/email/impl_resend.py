"""
Resend (https://resend.com) email transport.

A thin async client over the Resend HTTP API using the shared ``httpx``
dependency (already required by the OIDC flow) — no new package. The API key
is a Bearer token; the from-address domain must be a verified sending domain
in the Resend dashboard or the API returns 4xx.
"""

from __future__ import annotations

import httpx

from capelle_platform.email.base_sender import BaseEmailSender, EmailDeliveryError
from capelle_platform.observability import get_logger

_logger = get_logger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0


class ResendEmailSender(BaseEmailSender):
    """
    Deliver email via Resend's REST API.

    Stateless apart from the API key + from-address captured at construction.
    A dedicated short-lived :class:`httpx.AsyncClient` is opened per send so
    the sender holds no long-lived sockets and is safe to share across the app.
    """

    def __init__(self, *, api_key: str, email_from: str) -> None:
        """
        Args:
            api_key: Resend API key (``re_...``).
            email_from: RFC-5322 From header; its domain must be verified
                in Resend.
        """
        if not api_key:
            raise ValueError("ResendEmailSender requires a non-empty api_key")
        self._api_key = api_key
        self._from = email_from

    async def send(
        self, *, to: str, subject: str, html: str, text: str | None = None
    ) -> None:
        """POST the message to Resend; raise on a non-2xx response."""
        payload: dict[str, object] = {
            "from": self._from,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if text is not None:
            payload["text"] = text
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    _RESEND_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as exc:
            _logger.warning("resend_transport_error", to=to, error=str(exc))
            raise EmailDeliveryError(f"Resend transport error: {exc}") from exc

        if resp.status_code >= 400:
            # Body may carry a JSON error detail; log it without the key.
            _logger.warning(
                "resend_rejected",
                to=to,
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise EmailDeliveryError(
                f"Resend rejected the message: HTTP {resp.status_code}"
            )
        _logger.info("resend_sent", to=to, subject=subject)
